#!/usr/bin/env python3
"""Private tailnet forwarding and pairing for Orca in a userspace network."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
from urllib.parse import parse_qs, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'state' / 'remote-orca'
CLI = ROOT / 'bin' / 'orca'
TS = ROOT / 'bin' / 'tailscale'
PROFILE = Path(os.environ.get('ORCA_USER_DATA_PATH', ROOT / 'bin/orca.AppImage.config/orca'))
PAIRING_CODE_LIMIT = 128 * 1024
PAIRING_INPUT_LIMIT = PAIRING_CODE_LIMIT + 1024


def log(message, *, output=True):
    line = f'[{datetime.now().astimezone().isoformat(timespec="seconds")}] {message}'
    if output:
        print(line, flush=True)
    with (STATE / 'operations.log').open('a') as logfile:
        logfile.write(line + '\n')


def save(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.remote-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run(*args, capture=False):
    return subprocess.run([str(x) for x in args], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None)


def tailnet_ip():
    addresses = run(TS, 'ip', '-4', capture=True).stdout.strip().splitlines()
    if not addresses:
        raise ValueError('Tailscale has no IPv4 address; connect the tailnet first.')
    address = addresses[0]
    if ipaddress.ip_address(address) not in ipaddress.ip_network('100.64.0.0/10'):
        raise ValueError('Tailscale did not return a tailnet IPv4 address.')
    return address


def publish(port):
    address = tailnet_ip()
    run(TS, 'serve', '--bg', f'--tcp={port}', f'tcp://127.0.0.1:{port}')
    save(STATE / 'published.json', {'address': address, 'port': port})
    log(f'Tailnet forwarding: {address}:{port} -> 127.0.0.1:{port}')
    return address


def share():
    metadata = json.loads((PROFILE / 'orca-runtime.json').read_text())
    os.kill(int(metadata['pid']), 0)
    transports = metadata.get('transports', [metadata.get('transport', {})])
    endpoint = next(x['endpoint'] for x in transports if x.get('kind') == 'websocket')
    port = urlsplit(endpoint).port
    if not port:
        raise ValueError('The runtime metadata has no WebSocket port.')
    address = publish(port)
    print('\nIn this Orca desktop: click ⚙️, select Remote Orca Servers under Remote Hosts,')
    print('then New Link under "Advertise this app as a server".')
    print(f'Use connection address {address}, generate the access link, and paste it into')
    print('Remote Orca Servers -> Add Server on the other computer. Keep the access link private.')


def bridge_running(route_path, route):
    try:
        arguments = Path(f'/proc/{int(route.get("pid", 0))}/cmdline').read_bytes().split(b'\0')
        return (os.fsencode(str(Path(__file__).resolve())) in arguments and
                os.fsencode(str(route_path)) in arguments and b'_bridge' in arguments)
    except (OSError, ValueError):
        return False


def start_bridge(route_path):
    route = json.loads(route_path.read_text())
    if bridge_running(route_path, route):
        return route
    with route_path.with_suffix('.log').open('a') as output:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                    '_bridge', str(route_path)], stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=output, start_new_session=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Bridge exited; see {route_path.with_suffix(".log")}.')
        route = json.loads(route_path.read_text())
        if route.get('pid') == process.pid:
            log(f'Bridge running: 127.0.0.1:{route["local_port"]} -> '
                f'{route["remote_host"]}:{route["remote_port"]}')
            return route
        time.sleep(0.1)
    process.terminate()
    raise RuntimeError('Bridge did not become ready within 10 seconds.')


def read_exact(stream, size, deadline):
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Connection timed out.')
        stream.settimeout(remaining)
        data = stream.recv(size - len(result))
        if not data:
            raise EOFError('Connection closed.')
        result.extend(data)
    return bytes(result)


def read_http_headers(stream, deadline):
    header = bytearray()
    # Leave tunnel/WebSocket bytes untouched after the HTTP header terminator.
    while not header.endswith(b'\r\n\r\n'):
        if len(header) >= 32768:
            raise ValueError('HTTP headers exceed 32 KiB.')
        header.extend(read_exact(stream, 1, deadline))
    lines = bytes(header).decode('iso-8859-1').split('\r\n')
    headers = {}
    for line in lines[1:]:
        if line:
            key, value = line.split(':', 1)
            headers[key.lower().strip()] = value.strip()
    return lines[0], headers


def proxy_connection(route, timeout=20):
    """Use tailscaled's outbound HTTP proxy, independent of nc's LocalAPI stream."""
    deadline = time.monotonic() + timeout
    stream = socket.create_connection(('127.0.0.1', 1055), timeout=timeout)
    try:
        host = route['remote_host']
        authority = f'[{host}]:{route["remote_port"]}' if ':' in host else f'{host}:{route["remote_port"]}'
        stream.sendall(f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n'.encode('ascii'))
        status, _ = read_http_headers(stream, deadline)
        if status.split()[1:2] != ['200']:
            raise RuntimeError('Tailscale proxy: ' + status)
        stream.settimeout(None)
        return stream
    except BaseException:
        stream.close()
        raise


def websocket_send(stream, opcode, payload, deadline):
    # The probe sends only a small hello, a close frame, and optional pongs.
    if len(payload) > 125:
        raise ValueError('Probe frame exceeds 125 bytes.')
    mask = os.urandom(4)
    encoded = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Probe timed out.')
    stream.settimeout(remaining)
    stream.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + encoded)


def probe_orca(target, timeout):
    """Identify Orca's E2EE-ready response without supplying any pairing token."""
    deadline = time.monotonic() + timeout
    try:
        route = {'remote_host': target['address'], 'remote_port': target['port']}
        with proxy_connection(route, timeout=timeout) as stream:
            key = base64.b64encode(os.urandom(16)).decode('ascii')
            authority = f'{target["address"]}:{target["port"]}'
            request = (f'GET / HTTP/1.1\r\nHost: {authority}\r\nUpgrade: websocket\r\n'
                       'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                       f'Sec-WebSocket-Key: {key}\r\n\r\n')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            stream.settimeout(remaining)
            stream.sendall(request.encode('ascii'))
            status, headers = read_http_headers(stream, deadline)
            accept = base64.b64encode(hashlib.sha1(
                (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode('ascii'),
                usedforsecurity=False).digest()).decode('ascii')
            if (status.split()[1:2] != ['101'] or
                    headers.get('upgrade', '').lower() != 'websocket' or
                    headers.get('sec-websocket-accept') != accept):
                return None
            # Runtime clients use this public handshake before authentication.
            # A random 32-byte X25519 public input suffices: no shared secret or
            # authenticated request is needed to recognize the ready response.
            hello = json.dumps({'type': 'e2ee_hello',
                                'publicKeyB64': base64.b64encode(os.urandom(32)).decode('ascii')},
                               separators=(',', ':')).encode('ascii')
            websocket_send(stream, 1, hello, deadline)
            for _ in range(8):
                first, second = read_exact(stream, 2, deadline)
                if not first & 0x80 or first & 0x70 or second & 0x80:
                    return None
                opcode, length = first & 15, second & 127
                if length == 126:
                    length = int.from_bytes(read_exact(stream, 2, deadline), 'big')
                elif length == 127:
                    length = int.from_bytes(read_exact(stream, 8, deadline), 'big')
                if length > 65536 or (opcode >= 8 and length > 125):
                    return None
                payload = read_exact(stream, length, deadline)
                if opcode == 9:
                    websocket_send(stream, 10, payload, deadline)
                elif opcode == 10:
                    continue
                elif opcode == 1:
                    ready = json.loads(payload)
                    if isinstance(ready, dict) and ready.get('type') == 'e2ee_ready':
                        websocket_send(stream, 8, b'\x03\xe8', deadline)
                        return {**target, 'endpoint': f'ws://{authority}'}
                    return None
                else:
                    return None
    except (OSError, EOFError, ValueError, RuntimeError):
        return None
    return None


def import_remotes(ports, timeout, no_add, json_output):
    if json_output and not no_add:
        raise ValueError('Use --no-add with --json to preview servers without interactive pairing.')
    status = json.loads(run(TS, 'status', '--json', capture=True).stdout)
    if status.get('BackendState') != 'Running':
        raise ValueError('Connect Tailscale before importing Orca servers.')
    ports = sorted(set(ports or [6768]))
    if any(not 1 <= port <= 65535 for port in ports):
        raise ValueError('Ports must be between 1 and 65535.')
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError('Timeout must be greater than zero and at most 60 seconds.')
    peers = {}
    own_addresses = set((status.get('Self') or {}).get('TailscaleIPs') or [])
    for peer in (status.get('Peer') or {}).values():
        if peer.get('Online') is False:
            continue
        for address in peer.get('TailscaleIPs') or []:
            ip = ipaddress.ip_address(address)
            if address in own_addresses or ip.version != 4 or ip not in ipaddress.ip_network('100.64.0.0/10'):
                continue
            name = (peer.get('DNSName') or peer.get('HostName') or address).rstrip('.')
            peers[address] = {'name': name, 'address': address}
    targets = [{**peer, 'port': port} for peer in peers.values() for port in ports]
    log(f'Discovering Orca on {len(peers)} visible tailnet peers; ports: '
        + ', '.join(map(str, ports)), output=not json_output)
    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [entry for entry in pool.map(lambda target: probe_orca(target, timeout), targets) if entry]
    found.sort(key=lambda entry: (entry['name'], entry['address'], entry['port']))
    result = {'checked_peers': len(peers), 'ports': ports, 'servers': found}
    save(STATE / 'discovery.json', result)
    log(f'Discovery completed: {len(found)} Orca runtime(s) responded.', output=not json_output)
    if json_output:
        print(json.dumps(result, indent=2))
    elif found:
        width = max(len('TAILSCALE PEER'), *(len(entry['name']) for entry in found))
        print(f'{"TAILSCALE PEER":<{width}}  {"TAILNET IP":<15}  PORT   ENDPOINT')
        for entry in found:
            print(f'{entry["name"]:<{width}}  {entry["address"]:<15}  '
                  f'{entry["port"]:<5}  {entry["endpoint"]}')
        if no_add:
            print('\nPreview only. Run ./setup.sh remote import to add the servers, or use:')
            print('  ./setup.sh remote pair <connection-name>')
    else:
        print('No Orca runtimes responded on the selected ports. Check sharing, the runtime port,')
        print('the remote firewall, and tailnet access. Only visible, online IPv4 peers are checked.')
    if no_add or not found:
        return
    if not sys.stdin.isatty():
        raise ValueError('Adding servers needs an interactive terminal for access links. Use --no-add to preview.')
    listing = json.loads(run(CLI, 'environment', 'list', '--json', capture=True).stdout)
    configured = {entry['name'] for entry in listing.get('result', listing).get('environments', [])}
    routes = {}
    for path in STATE.glob('*.route.json'):
        route = json.loads(path.read_text())
        routes[path.name.removesuffix('.route.json')] = route
    used = configured | set(routes)
    failures = 0
    for server in found:
        matching = [name for name, route in routes.items()
                    if (route['remote_host'], route['remote_port']) ==
                    (server['address'], server['port'])]
        name = next((name for name in matching if name in configured), next(iter(matching), None))
        if name is None:
            base = re.sub(r'[^A-Za-z0-9_.-]+', '-', server['name']).strip('._-')
            base = base or 'orca-' + server['address'].replace('.', '-')
            if server['port'] != 6768:
                base += f'-{server["port"]}'
            name = base
            suffix = 2
            while name in used:
                name = f'{base}-{suffix}'
                suffix += 1
        used.add(name)
        server['connection_name'] = name
        print(f'\n{server["name"]} at {server["endpoint"]} -> Orca connection {name}')
        try:
            if pair(name, target=server, allow_skip=True):
                server['action'] = 'reused' if name in configured else 'added'
                configured.add(name)
            else:
                server['action'] = 'skipped'
        except (OSError, ValueError, RuntimeError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
            # Do not stringify CalledProcessError: argv can contain a pairing link.
            message = (f'Orca CLI exited with status {error.returncode}; see its terminal output.'
                       if isinstance(error, subprocess.CalledProcessError) else str(error))
            log(f'Could not add {name}: {message}')
            server['action'] = 'failed'
            failures += 1
        save(STATE / 'discovery.json', result)
    log('Import finished: ' + ', '.join(
        f'{sum(server.get("action") == action for server in found)} {action}'
        for action in ('added', 'reused', 'skipped', 'failed')))
    if failures:
        raise RuntimeError('Some servers could not be added; see the terminal and operations.log.')


def relay(client, route):
    upstream = None
    upload = None

    def pump(source, destination):
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    return
                destination.sendall(data)
        except OSError:
            pass
        finally:
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    try:
        upstream = proxy_connection(route)
        upload = threading.Thread(target=pump, args=(client, upstream), daemon=True)
        upload.start()
        pump(upstream, client)
    except (OSError, EOFError, ValueError, RuntimeError) as error:
        print(f'Bridge connection failed: {error}', flush=True)
    finally:
        for stream in (client, upstream):
            if stream:
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                stream.close()
        if upload:
            upload.join(timeout=1)


def bridge(route_path):
    route = json.loads(route_path.read_text())
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', route.get('local_port', 0)))
        listener.listen(32)
        route.update(local_port=listener.getsockname()[1], pid=os.getpid())
        save(route_path, route)
        print(f'Listening on 127.0.0.1:{route["local_port"]}', flush=True)
        while True:
            client, _ = listener.accept()
            threading.Thread(target=relay, args=(client, route), daemon=True).start()


def parse_access_link(value, target=None):
    # Clipboard line wrapping and surrounding shell/Markdown quotes are harmless.
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '\"\'`':
        value = value[1:-1]
    value = ''.join(value.split())
    if len(value) > PAIRING_INPUT_LIMIT:
        raise ValueError('The access link is too long. Copy a newly generated runtime access link.')
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise ValueError('The access link has an invalid URL format. Copy the complete orca://pair?code=... link.') from None
    if parsed.scheme == 'orca':
        if parsed.netloc.lower() != 'pair' or parsed.path not in ('', '/'):
            raise ValueError('Expected orca://pair?code=...; copy the complete link under "Pair another Orca client".')
        code = parse_qs(parsed.query).get('code', [parsed.fragment])[0]
        if not code:
            raise ValueError('The link has no pairing code. Copy the complete orca://pair?code=... link.')
    elif parsed.scheme:
        raise ValueError('A server address is not an access link. Copy the orca://pair?code=... link under "Pair another Orca client".')
    else:
        code = value
    if not code or len(code) > PAIRING_CODE_LIMIT or not re.fullmatch(r'[A-Za-z0-9+/_-]+={0,2}', code):
        raise ValueError('The pairing code is incomplete or contains unexpected characters. Copy the complete access link again.')
    try:
        offer = json.loads(base64.b64decode(code + '=' * (-len(code) % 4), altchars=b'-_', validate=True))
    except (ValueError, UnicodeError):
        raise ValueError('The pairing code is incomplete or damaged. Copy the complete access link again.') from None
    if not isinstance(offer, dict):
        raise ValueError('The link does not contain an Orca pairing offer. Generate a new runtime access link.')
    if offer.get('scope') != 'runtime':
        raise ValueError('Use the link under "Pair another Orca client", not a mobile pairing link.')
    if offer.get('v') != 2:
        raise ValueError('Unsupported access link version. Update Orca on both computers and generate a new link.')
    if any(not isinstance(offer.get(key), str) or not offer[key] for key in ('deviceToken', 'publicKeyB64', 'endpoint')):
        raise ValueError('The access link is missing runtime connection details. Generate a new runtime access link.')
    try:
        endpoint = urlsplit(offer['endpoint'])
        port = endpoint.port or 80
        if endpoint.port == 0 or any(ord(character) < 32 or ord(character) == 127 for character in offer['endpoint']):
            raise ValueError
    except ValueError:
        raise ValueError('The access link has an invalid server address. Generate a new runtime access link.') from None
    if endpoint.scheme != 'ws' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.fragment:
        raise ValueError('Use the private ws:// Tailscale access link generated by Orca.')
    try:
        address = ipaddress.ip_address(endpoint.hostname)
    except ValueError:
        raise ValueError('Generate the access link with the remote host\'s 100.x Tailscale IP.') from None
    if address.version != 4 or address not in ipaddress.ip_network('100.64.0.0/10'):
        raise ValueError('Generate the access link with the remote host\'s 100.x Tailscale IP.')
    if target and (endpoint.hostname, port) != (target['address'], target['port']):
        raise ValueError('The access link must advertise this discovered server\'s tailnet IP and port.')
    return offer, endpoint


def prompt_access_link(*, target=None, allow_skip=False):
    """Show paste progress and format feedback without echoing credentials."""
    print('Paste the complete orca://pair?code=... link from "Pair another Orca client".')
    print('The character count and format status are visible; the code stays hidden.')
    print('Enter submits; Ctrl-U clears; ' + ('Enter on an empty line skips.' if allow_skip else 'Ctrl-C cancels.'))
    try:
        descriptor = os.open('/dev/tty', os.O_RDWR | os.O_NOCTTY)
    except OSError:
        raise ValueError('Pairing needs an interactive terminal for the access link.') from None
    attributes = None
    line_open = False
    value = bytearray()
    escape = bytearray()
    pasting = False

    def write(message):
        os.write(descriptor, message.encode())

    def render():
        nonlocal line_open
        text = value.decode('utf-8', errors='replace')
        if not text:
            status = 'waiting for paste'
        else:
            try:
                _, endpoint = parse_access_link(text, target)
                status = f'{len(text)} chars; format OK for {endpoint.hostname}:{endpoint.port or 80}'
            except ValueError:
                status = f'{len(text)} chars; incomplete/invalid format'
        columns = os.get_terminal_size(descriptor).columns or 80
        prefix, suffix = 'Paste access link [', ']: '
        width = max(0, columns - len(prefix) - len(suffix) - 1)
        write('\r\x1b[2K' + prefix + status[:width] + suffix)
        line_open = True

    try:
        attributes = termios.tcgetattr(descriptor)
        # Canonical terminal input can truncate long pasted links at 4095 bytes.
        tty.setraw(descriptor)
        write('\x1b[?2004h')
        render()
        while True:
            data = os.read(descriptor, 4096)
            if not data:
                raise ValueError('Input closed before an access link was submitted.')
            for character in data:
                if character == 3:
                    raise KeyboardInterrupt
                if escape:
                    escape.append(character)
                    if len(escape) == 2 and character not in (ord('['), ord('O')):
                        escape.clear()
                    elif len(escape) > 2 and 64 <= character <= 126:
                        if escape == b'\x1b[200~':
                            pasting = True
                        elif escape == b'\x1b[201~':
                            pasting = False
                        escape.clear()
                    elif len(escape) > 32:
                        escape.clear()
                    continue
                if character == 27:
                    escape.append(character)
                elif pasting:
                    # Bracketed paste may contain wrapped lines; only Enter
                    # after the paste ends submits the complete link.
                    if character > 32 and character != 127 and len(value) <= PAIRING_INPUT_LIMIT:
                        value.append(character)
                elif character in (10, 13):
                    text = value.decode('utf-8', errors='replace').strip()
                    write('\r\x1b[2K')
                    if allow_skip and not text:
                        write('No access link supplied; skipping.\r\n')
                        line_open = False
                        return None
                    try:
                        offer, endpoint = parse_access_link(text, target)
                    except ValueError as error:
                        write(f'Could not use that link: {error}\r\nPaste again, or '
                              + ('press Enter to skip.\r\n' if allow_skip else 'press Ctrl-C to cancel.\r\n'))
                        value.clear()
                    else:
                        write(f'Received {len(text)} characters. Format OK: runtime access for '
                              f'{endpoint.hostname}:{endpoint.port or 80}.\r\n')
                        line_open = False
                        return offer, endpoint
                elif character == 4:
                    raise ValueError('Input closed before an access link was submitted.')
                elif character in (21, 23):
                    value.clear()
                elif character in (8, 127):
                    if value:
                        value.pop()
                elif character >= 32 and len(value) <= PAIRING_INPUT_LIMIT:
                    value.append(character)
            render()
    finally:
        try:
            if line_open:
                write('\r\n')
            if attributes is not None:
                write('\x1b[?2004l')
        finally:
            try:
                if attributes is not None:
                    termios.tcsetattr(descriptor, termios.TCSADRAIN, attributes)
            finally:
                os.close(descriptor)


def pair(name, *, target=None, allow_skip=False):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise ValueError('Use an Orca connection name containing letters, numbers, dots, dashes or underscores.')
    print(f'Orca connection name: {name} (independent of the Tailscale machine name).')
    route_path = STATE / f'{name}.route.json'
    listing = json.loads(run(CLI, 'environment', 'list', '--json', capture=True).stdout)
    environments = listing.get('result', listing).get('environments', [])
    if any(entry.get('name') == name for entry in environments):
        if not route_path.exists():
            raise ValueError(f'A server named {name} already exists without a saved bridge. '
                             'Choose a different name, or remove that server in Orca before pairing again.')
        route = json.loads(route_path.read_text())
        if target and (route['remote_host'], route['remote_port']) != (target['address'], target['port']):
            raise ValueError(f'The saved connection {name} points to a different server.')
        start_bridge(route_path)
        log(f'Reusing saved Remote Orca Server {name}')
        run(CLI, 'host', 'name', '--environment', name)
        return True
    print('On the remote computer, generate a runtime access link using its Tailscale IP address.')
    # Never put the original access link into shell history or operation logs.
    parsed_link = prompt_access_link(target=target, allow_skip=allow_skip)
    if parsed_link is None:
        log(f'Skipped {name}; no access link supplied.')
        return False
    offer, endpoint = parsed_link
    old = json.loads(route_path.read_text()) if route_path.exists() else {}
    destination = {'remote_host': endpoint.hostname, 'remote_port': endpoint.port or 80}
    if old and (old['remote_host'], old['remote_port']) != (destination['remote_host'], destination['remote_port']):
        if bridge_running(route_path, old):
            os.kill(old['pid'], signal.SIGTERM)
        old = {}
    save(route_path, {**old, **destination})
    route = start_bridge(route_path)
    # Only the endpoint changes. Preserve Orca's token, server public key and E2EE.
    offer['endpoint'] = urlunsplit(('ws', f'127.0.0.1:{route["local_port"]}',
                                    endpoint.path, endpoint.query, ''))
    payload = base64.urlsafe_b64encode(json.dumps(offer).encode()).decode().rstrip('=')
    link = 'orca://pair?code=' + payload
    # Orca's CLI validates the version-matched schema and persists the credentials.
    run(CLI, 'environment', 'add', '--name', name, '--pairing-code', link)
    log(f'Saved Remote Orca Server {name}; credentials remain in the private Orca profile.')
    log(f'Checking the connection to {name}')
    run(CLI, 'host', 'name', '--environment', name)
    print(f'Use the saved server in Orca, or run: bin/orca host name --environment {name}')
    return True


def resume():
    for route_path in STATE.glob('*.route.json'):
        start_bridge(route_path)


def main():
    os.umask(0o077)
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('share', help='Forward the running desktop runtime into the tailnet.')
    commands.add_parser('resume', help='Restore saved outgoing loopback bridges.')
    importing = commands.add_parser('import', help='Find tailnet Orca runtimes and add them using access links.')
    importing.add_argument('--port', metavar='<port>', type=int, action='append',
                           help='Runtime port to probe; repeat for multiple ports. Default: 6768.')
    importing.add_argument('--timeout', metavar='<seconds>', type=float, default=5,
                           help='Per-probe deadline in seconds (default: 5, maximum: 60).')
    importing.add_argument('--no-add', action='store_true', help='Preview discovered servers without adding any.')
    importing.add_argument('--json', action='store_true', help='Output JSON; requires --no-add.')
    pairing = commands.add_parser('pair', help='Save another server using its private access link.')
    pairing.add_argument('name', metavar='<connection-name>',
                         help='Your chosen local Orca connection name; the destination comes from the pasted access link.')
    internal = commands.add_parser('_bridge', help=argparse.SUPPRESS)
    internal.add_argument('route', metavar='<route-file>', type=Path)
    args = parser.parse_args()
    if args.command == '_bridge':
        bridge(args.route)
    elif args.command == 'share':
        share()
    elif args.command == 'pair':
        pair(args.name)
    elif args.command == 'import':
        import_remotes(args.port, args.timeout, args.no_add, args.json)
    elif args.command == 'resume':
        resume()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
        # CalledProcessError includes argv, which may contain a pairing token.
        message = (f'Command failed with exit {error.returncode}; see its terminal output.'
                   if isinstance(error, subprocess.CalledProcessError) else str(error))
        log('ERROR: ' + message)
        sys.exit(1)
