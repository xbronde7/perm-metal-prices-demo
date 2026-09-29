from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

import requests


class RobotsPolicy:
    def __init__(self, raw):
        groups=[];agents=[];rules=[]
        for line in raw.splitlines()+["User-agent: END"]:
            line=line.split("#",1)[0].strip()
            if ":" not in line:continue
            key,val=(p.strip() for p in line.split(":",1))
            if key.lower()=="user-agent":
                if rules:
                    groups.append((agents,rules));agents=[];rules=[]
                agents.append(val.lower())
            elif key.lower() in {"allow","disallow"} and agents and val:
                rules.append((key.lower()=="allow",val))
        exact=[r for a,r in groups if any(x!="*" and x in "permprocurementradar" for x in a)]
        self.rules=[rule for rs in (exact or [r for a,r in groups if "*" in a]) for rule in rs]

    def can_fetch(self, agent, url):
        p=urlparse(url);target=p.path+("?"+p.query if p.query else "")
        matches=[]
        for allow,pattern in self.rules:
            ends=pattern.endswith("$");pattern=pattern.rstrip("$")
            regex="^"+".*".join(re.escape(part) for part in pattern.split("*"))+("$" if ends else "")
            if re.search(regex,target):matches.append((len(pattern.replace("*","")),allow))
        return max(matches)[1] if matches else True


class Fetcher:
    """Bounded public fetches; archive originals, enforce host rate and redirects."""
    def __init__(self, directory: Path, delay=0.4, max_requests=220):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "PermProcurementRadar/1.0 (public procurement and price monitoring)"
        self.delay, self.remaining = delay, max_requests
        self.last, self.robots, self.cache = {}, {}, {}

    @staticmethod
    def validate(url):
        p = urlparse(url)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.port not in {None, 80, 443}:
            raise ValueError("Только публичные HTTP(S) URL без авторизации")
        for info in socket.getaddrinfo(p.hostname, p.port or 443, type=socket.SOCK_STREAM):
            if not ipaddress.ip_address(info[4][0]).is_global:
                raise ValueError("Локальный или служебный адрес запрещён")

    def _request(self, url):
        for _ in range(5):
            self.validate(url)
            if self.remaining <= 0:
                raise ValueError("Лимит запросов за цикл исчерпан")
            self.remaining -= 1
            host = urlparse(url).hostname
            time.sleep(max(0, self.delay - (time.monotonic() - self.last.get(host, 0))))
            self.last[host] = time.monotonic()
            r = self.session.get(url, timeout=(6, 18), allow_redirects=False, stream=True)
            if r.is_redirect:
                url = urljoin(url, r.headers["Location"])
                r.close()
                continue
            r.raise_for_status()
            if int(r.headers.get("Content-Length", "0")) > 15_000_000:
                r.close()
                raise ValueError("Файл больше 15 МБ")
            parts, size = [], 0
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > 15_000_000:
                    r.close()
                    raise ValueError("Файл больше 15 МБ")
                parts.append(chunk)
            r.close()
            return b"".join(parts), dict(r.headers), url
        raise ValueError("Слишком много перенаправлений")

    def fetch(self, url, robots=True):
        if url in self.cache:
            return self.cache[url]
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if robots:
            if origin not in self.robots:
                try:
                    blob, _, _ = self._request(origin + "/robots.txt")
                    self.robots[origin] = RobotsPolicy(blob.decode("utf-8", "replace"))
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code == 404:
                        self.robots[origin] = None
                    else:
                        raise ValueError("robots.txt недоступен; обход отложен") from None
                except Exception:
                    raise ValueError("robots.txt недоступен; обход отложен") from None
            rules = self.robots[origin]
            if rules and not rules.can_fetch("PermProcurementRadar", url):
                raise ValueError("Обход запрещён robots.txt")
        blob, headers, final_url = self._request(url)
        digest = hashlib.sha256(blob).hexdigest()
        path = self.directory / digest
        if not path.exists():
            path.write_bytes(blob)
        meta = {"url":url,"finalUrl":final_url,"sha256":digest,"contentType":next((v for k,v in headers.items() if k.lower()=="content-type"),None),"bytes":len(blob)}
        (self.directory / f"{digest}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        result = (blob, meta)
        self.cache[url] = result
        return result


def error_message(exc):
    # Never expose request headers, environment values, or query credentials in UI.
    if isinstance(exc, requests.HTTPError):
        return f"HTTP {exc.response.status_code}" if exc.response is not None else "Ошибка HTTP"
    if isinstance(exc, requests.Timeout):
        return "Источник не ответил вовремя"
    if isinstance(exc, requests.RequestException):
        return "Ошибка соединения с источником"
    return re.sub(r"https?://\S+", "[URL]", str(exc))[:160]
