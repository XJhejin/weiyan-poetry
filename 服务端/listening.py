"""Validate literal bind addresses and advertise only matching endpoints."""
import ipaddress
import socket


def listen_address(value):
    if not isinstance(value, str):
        raise ValueError('请输入本机的 IPv4 或 IPv6 地址。')
    value = value.strip()
    if value.startswith('[') and value.endswith(']'):
        value = value[1:-1]
    if value.lower() == 'localhost':
        value = '127.0.0.1'
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise ValueError('请输入有效的本机 IPv4 或 IPv6 地址，不含协议、端口或网段。') from error
    if '%' in value or address.is_multicast or value == '255.255.255.255':
        raise ValueError('请填写普通本机 IP 地址，或使用 0.0.0.0 / :: 监听全部网卡。')
    return str(address)


def listen_port(value):
    text = str(value).strip()
    if isinstance(value, bool) or not text.isascii() or not text.isdecimal() or not 1 <= int(text) <= 65535:
        raise ValueError('服务端口应为 1 至 65535 的整数。')
    return int(text)


def connection_addresses(host, port):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return []
    hosts = [str(address)]
    if address.is_unspecified:
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        hosts = ['::1' if address.version == 6 else '127.0.0.1']
        try:
            for item in socket.getaddrinfo(socket.gethostname(), None, family, socket.SOCK_STREAM):
                value = item[4][0]
                candidate = ipaddress.ip_address(value)
                if (not candidate.is_loopback and not candidate.is_unspecified
                        and not candidate.is_link_local and '%' not in value and value not in hosts):
                    hosts.append(value)
        except (OSError, ValueError):
            pass
    return [f'http://[{value}]:{port}' if ':' in value else f'http://{value}:{port}' for value in hosts]
