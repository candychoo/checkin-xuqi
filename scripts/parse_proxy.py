#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多协议代理链接解析器
支持: hysteria2, hy2, tuic, vless, vmess, trojan, ss (shadowsocks), socks
输出: sing-box config.json，并打印关键字段
"""
import urllib.parse
import json
import base64
import os
import sys


def b64decode_safe(s: str) -> bytes:
    """Safe base64 decode (handle missing padding)."""
    s = s.strip()
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def parse_hysteria2(link: str) -> dict:
    """Parse hysteria2:// or hy2:// link."""
    for prefix in ("hysteria2://", "hy2://"):
        if link.startswith(prefix):
            link = link[len(prefix):]
            break
    else:
        raise ValueError(f"not a hysteria2 link: {link[:30]}")

    link = link.split("#", 1)[0]
    main, _, query = link.partition("?")

    if "@" not in main:
        raise ValueError("no @ in link")
    password_raw, server_part = main.rsplit("@", 1)
    password = urllib.parse.unquote(password_raw)

    if ":" not in server_part:
        raise ValueError("no port")
    server, port_str = server_part.rsplit(":", 1)
    port = int(port_str)

    params = urllib.parse.parse_qs(query)
    sni = params.get("sni", [""])[0]
    insecure = any(
        params.get(k, ["0"])[0] in ("1", "true")
        for k in ("insecure", "allowInsecure")
    )
    alpn = params.get("alpn", [""])[0]

    outbound = {
        "type": "hysteria2",
        "tag": "proxy",
        "server": server,
        "server_port": port,
        "password": password,
        "tls": {
            "enabled": True,
            "server_name": sni or server,
            "insecure": insecure,
        },
    }
    if alpn:
        outbound["tls"]["alpn"] = alpn.split(",")
    return outbound


def parse_tuic(link: str) -> dict:
    """Parse tuic:// link."""
    if not link.startswith("tuic://"):
        raise ValueError(f"not a tuic link: {link[:30]}")
    link = link[len("tuic://"):]
    link = link.split("#", 1)[0]
    main, _, query = link.partition("?")

    if "@" not in main:
        raise ValueError("no @ in link")
    user_info, server_part = main.rsplit("@", 1)

    if ":" not in user_info:
        raise ValueError("no uuid:password")
    uuid, password = user_info.split(":", 1)
    uuid = urllib.parse.unquote(uuid)
    password = urllib.parse.unquote(password)

    if ":" not in server_part:
        raise ValueError("no port")
    server, port_str = server_part.rsplit(":", 1)
    port = int(port_str)

    params = urllib.parse.parse_qs(query)
    sni = params.get("sni", [""])[0]
    insecure = any(
        params.get(k, ["0"])[0] in ("1", "true")
        for k in ("insecure", "allowInsecure")
    )
    alpn = params.get("alpn", [""])[0]

    outbound = {
        "type": "tuic",
        "tag": "proxy",
        "server": server,
        "server_port": port,
        "uuid": uuid,
        "password": password,
        "tls": {
            "enabled": True,
            "server_name": sni or server,
            "insecure": insecure,
        },
    }
    if alpn:
        outbound["tls"]["alpn"] = alpn.split(",")
    return outbound


def parse_vless(link: str) -> dict:
    """Parse vless:// link."""
    if not link.startswith("vless://"):
        raise ValueError(f"not a vless link: {link[:30]}")
    link = link[len("vless://"):]
    link = link.split("#", 1)[0]
    main, _, query = link.partition("?")

    if "@" not in main:
        raise ValueError("no @ in link")
    uuid, server_part = main.rsplit("@", 1)

    if ":" not in server_part:
        raise ValueError("no port")
    server, port_str = server_part.rsplit(":", 1)
    port = int(port_str)

    params = urllib.parse.parse_qs(query)
    sni = params.get("sni", [""])[0]
    insecure = any(
        params.get(k, ["0"])[0] in ("1", "true")
        for k in ("insecure", "allowInsecure")
    )
    flow = params.get("flow", [""])[0]
    type_ = params.get("type", ["tcp"])[0]
    security = params.get("security", ["none"])[0]

    outbound = {
        "type": "vless",
        "tag": "proxy",
        "server": server,
        "server_port": port,
        "uuid": uuid,
    }
    if flow:
        outbound["flow"] = flow

    if security == "tls":
        outbound["tls"] = {
            "enabled": True,
            "server_name": sni or server,
            "insecure": insecure,
        }
    elif security == "reality":
        outbound["tls"] = {
            "enabled": True,
            "server_name": sni or server,
            "reality": {
                "enabled": True,
                "public_key": params.get("pbk", [""])[0],
                "short_id": params.get("sid", [""])[0],
            },
        }

    if type_ == "ws":
        outbound["transport"] = {
            "type": "ws",
            "path": params.get("path", ["/"])[0],
            "headers": {"Host": params.get("host", [server])[0]},
        }
    elif type_ == "grpc":
        outbound["transport"] = {
            "type": "grpc",
            "service_name": params.get("serviceName", [""])[0],
        }
    return outbound


def parse_vmess(link: str) -> dict:
    """Parse vmess:// link (base64 JSON)."""
    if not link.startswith("vmess://"):
        raise ValueError(f"not a vmess link: {link[:30]}")
    body = link[len("vmess://"):]
    data = json.loads(b64decode_safe(body).decode("utf-8"))

    server = data["add"]
    port = int(data["port"])
    uuid = data["id"]
    net = data.get("net", "tcp")
    tls = data.get("tls", "")
    sni = data.get("sni", "") or data.get("host", "") or server
    path = data.get("path", "/")
    host = data.get("host", "") or server

    outbound = {
        "type": "vmess",
        "tag": "proxy",
        "server": server,
        "server_port": port,
        "uuid": uuid,
        "security": data.get("scy", "auto"),
        "alter_id": int(data.get("aid", 0)),
    }

    if tls == "tls":
        outbound["tls"] = {
            "enabled": True,
            "server_name": sni,
        }

    if net == "ws":
        outbound["transport"] = {
            "type": "ws",
            "path": path,
            "headers": {"Host": host},
        }
    elif net == "grpc":
        outbound["transport"] = {
            "type": "grpc",
            "service_name": data.get("path", ""),
        }
    return outbound


def parse_trojan(link: str) -> dict:
    """Parse trojan:// link."""
    if not link.startswith("trojan://"):
        raise ValueError(f"not a trojan link: {link[:30]}")
    link = link[len("trojan://"):]
    link = link.split("#", 1)[0]
    main, _, query = link.partition("?")

    if "@" not in main:
        raise ValueError("no @ in link")
    password, server_part = main.rsplit("@", 1)
    password = urllib.parse.unquote(password)

    if ":" not in server_part:
        raise ValueError("no port")
    server, port_str = server_part.rsplit(":", 1)
    port = int(port_str)

    params = urllib.parse.parse_qs(query)
    sni = params.get("sni", [""])[0]
    insecure = any(
        params.get(k, ["0"])[0] in ("1", "true")
        for k in ("insecure", "allowInsecure")
    )
    type_ = params.get("type", ["tcp"])[0]

    outbound = {
        "type": "trojan",
        "tag": "proxy",
        "server": server,
        "server_port": port,
        "password": password,
        "tls": {
            "enabled": True,
            "server_name": sni or server,
            "insecure": insecure,
        },
    }

    if type_ == "ws":
        outbound["transport"] = {
            "type": "ws",
            "path": params.get("path", ["/"])[0],
            "headers": {"Host": params.get("host", [server])[0]},
        }
    elif type_ == "grpc":
        outbound["transport"] = {
            "type": "grpc",
            "service_name": params.get("serviceName", [""])[0],
        }
    return outbound


def parse_ss(link: str) -> dict:
    """Parse ss:// link (shadowsocks)."""
    if not link.startswith("ss://"):
        raise ValueError(f"not a ss link: {link[:30]}")
    link = link[len("ss://"):]
    link = link.split("#", 1)[0]

    # Two formats:
    # 1. ss://base64(method:password)@server:port
    # 2. ss://base64(method:password@server:port)
    if "@" in link:
        userinfo_b64, server_part = link.rsplit("@", 1)
        try:
            userinfo = b64decode_safe(userinfo_b64).decode("utf-8")
        except Exception:
            userinfo = urllib.parse.unquote(userinfo_b64)
    else:
        try:
            decoded = b64decode_safe(link).decode("utf-8")
        except Exception:
            raise ValueError("cannot decode ss link")
        if "@" not in decoded:
            raise ValueError("no @ in decoded ss link")
        userinfo, server_part = decoded.rsplit("@", 1)

    if ":" not in userinfo:
        raise ValueError("no method:password")
    method, password = userinfo.split(":", 1)

    if ":" not in server_part:
        raise ValueError("no port")
    server, port_str = server_part.rsplit(":", 1)
    port = int(port_str)

    return {
        "type": "shadowsocks",
        "tag": "proxy",
        "server": server,
        "server_port": port,
        "method": method,
        "password": password,
    }


def parse_socks(link: str) -> dict:
    """Parse socks5:// or socks:// link."""
    for prefix in ("socks5://", "socks://", "socks4://"):
        if link.startswith(prefix):
            link = link[len(prefix):]
            stype = "socks4" if prefix == "socks4://" else "socks5"
            break
    else:
        raise ValueError(f"not a socks link: {link[:30]}")

    link = link.split("#", 1)[0]
    if "@" in link:
        userinfo, server_part = link.rsplit("@", 1)
        username, _, password = userinfo.partition(":")
        password = urllib.parse.unquote(password)
        username = urllib.parse.unquote(username)
    else:
        server_part = link
        username = password = ""

    if ":" not in server_part:
        raise ValueError("no port")
    server, port_str = server_part.rsplit(":", 1)
    port = int(port_str)

    outbound = {
        "type": "socks",
        "tag": "proxy",
        "server": server,
        "server_port": port,
        "version": stype,
    }
    if username:
        outbound["username"] = username
    if password:
        outbound["password"] = password
    return outbound


PARSERS = [
    ("hysteria2://", parse_hysteria2),
    ("hy2://", parse_hysteria2),
    ("tuic://", parse_tuic),
    ("vless://", parse_vless),
    ("vmess://", parse_vmess),
    ("trojan://", parse_trojan),
    ("ss://", parse_ss),
    ("socks5://", parse_socks),
    ("socks://", parse_socks),
    ("socks4://", parse_socks),
]


def parse(link: str) -> dict:
    link = link.strip()
    for prefix, parser in PARSERS:
        if link.startswith(prefix):
            return parser(link)
    raise ValueError(f"unsupported protocol: {link[:30]}")


def main():
    link = os.environ.get("NODE_LINK", "").strip()
    if not link:
        print("ERROR: NODE_LINK empty", file=sys.stderr)
        sys.exit(1)

    try:
        outbound = parse(link)
    except Exception as e:
        print(f"ERROR: parse failed: {e}", file=sys.stderr)
        sys.exit(1)

    config = {
        "log": {"level": "info"},
        "inbounds": [
            {"type": "socks", "tag": "socks-in", "listen": "127.0.0.1", "listen_port": 1080},
            {"type": "http", "tag": "http-in", "listen": "127.0.0.1", "listen_port": 20170},
        ],
        "outbounds": [outbound],
    }

    with open("/tmp/sing-box-config.json", "w") as f:
        json.dump(config, f, indent=2)

    # Print summary for log
    proto = outbound["type"]
    server = outbound.get("server", "")
    port = outbound.get("server_port", 0)
    print(f"PROTOCOL={proto}")
    print(f"SERVER={server}")
    print(f"PORT={port}")

    # Print protocol-specific info
    if proto in ("hysteria2", "tuic", "trojan"):
        pwd = outbound.get("password", "")
        print(f"PASSWORD_LEN={len(pwd)}")
        print(f"PASSWORD_PREFIX={pwd[:10]}...")
    if proto == "tuic":
        print(f"UUID={outbound.get('uuid', '')}")
    if proto == "vless":
        print(f"UUID={outbound.get('uuid', '')}")
    if proto == "vmess":
        print(f"UUID={outbound.get('uuid', '')}")
    if proto == "shadowsocks":
        print(f"METHOD={outbound.get('method', '')}")

    if "tls" in outbound:
        tls = outbound["tls"]
        print(f"SNI={tls.get('server_name', '')}")
        print(f"INSECURE={tls.get('insecure', False)}")
    if "transport" in outbound:
        print(f"TRANSPORT={outbound['transport']['type']}")


if __name__ == "__main__":
    main()
