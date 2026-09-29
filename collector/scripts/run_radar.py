"""Run one collection or serve the dashboard with a recurring collector.

python scripts/run_radar.py once
python scripts/run_radar.py serve --web-dir ../perm-metal-fallback/dist
Secrets: TYPESAFE_API_KEY / TYPESAFE_MODEL / SEARXNG_URL / RADAR_ADMIN_TOKEN.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import signal
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from radar.domain import now_iso
from radar.pipeline import atomic_json, run

ROOT=Path(__file__).resolve().parents[1]


def load_env(path):
    if not path.exists():return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:continue
        k,v=line.split("=",1)
        if k.strip() in {"TYPESAFE_API_KEY","TYPESAFE_MODEL","SEARXNG_URL","RADAR_ADMIN_TOKEN"}:
            os.environ.setdefault(k.strip(),v.strip().strip('"\''))


class Collector:
    def __init__(self,args):
        self.args=args
        self.lock=threading.Lock()
        self.stop=threading.Event()
        self.status={"running":False,"lastError":None,"lastFinishedAt":None}

    def collect(self):
        if not self.lock.acquire(blocking=False):return False
        self.status.update(running=True,lastError=None,startedAt=now_iso())
        try:
            # Lock across CLI/server instances on Linux and macOS.
            import fcntl
            self.args.state.mkdir(parents=True,exist_ok=True)
            with (self.args.state/"run.lock").open("w") as file:
                fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
                result=run(self.args.config,self.args.snapshot,self.args.output,self.args.state,not self.args.skip_prices)
                result["automation"]["mode"]="scheduled_service" if self.args.command=="serve" else self.args.scheduler
                atomic_json(self.args.output,result)
            self.status["lastFinishedAt"]=now_iso()
            return True
        except Exception as exc:
            self.status["lastError"]=type(exc).__name__+": collection failed; see source report"
            if self.args.command=="once":raise
            return False
        finally:
            self.status["running"]=False
            self.lock.release()

    def schedule(self):
        config=json.loads(self.args.config.read_text(encoding="utf-8"))
        interval=max(15,config.get("intervalMinutes",240))*60
        while not self.stop.is_set():
            self.collect()
            self.stop.wait(interval)


def serve(args):
    token=os.environ.get("RADAR_ADMIN_TOKEN","")
    if args.host not in {"127.0.0.1","localhost","::1"} and not token:
        raise SystemExit("Для внешнего сервера задайте RADAR_ADMIN_TOKEN и поставьте HTTPS reverse proxy")
    collector=Collector(args)
    static=args.web_dir.resolve()
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw):super().__init__(*a,directory=str(static),**kw)

        def respond(self,data,status=200):
            raw=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(raw)));self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(raw)

        def do_GET(self):
            path=urlparse(self.path).path
            if path=="/api/radar/status":return self.respond(collector.status)
            if path.endswith("/data/deals.json") or path=="/api/radar":
                if not args.output.exists():return self.respond({"error":"Первый сбор ещё выполняется"},503)
                return self.respond(json.loads(args.output.read_text(encoding="utf-8")))
            if path.endswith("/data/snapshot.json") and args.snapshot.exists():
                return self.respond(json.loads(args.snapshot.read_text(encoding="utf-8")))
            if path.startswith("/perm-metal-prices-demo/"):
                self.path=path.removeprefix("/perm-metal-prices-demo") or "/"
            super().do_GET()

        def do_POST(self):
            if urlparse(self.path).path!="/api/radar/refresh":return self.respond({"error":"Не найдено"},404)
            if self.headers.get("Content-Type","").split(";")[0]!="application/json":return self.respond({"error":"Ожидается JSON"},415)
            origin=self.headers.get("Origin")
            if origin and urlparse(origin).netloc!=self.headers.get("Host"):
                return self.respond({"error":"Источник запроса не разрешён"},403)
            if token and not hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+token):
                return self.respond({"error":"Нужен токен оператора"},401)
            if not token and self.headers.get("Host","").split(":")[0] not in {"127.0.0.1","localhost"}:
                return self.respond({"error":"Недопустимый адрес сервера"},403)
            if collector.status["running"]:return self.respond({"error":"Сбор уже выполняется"},409)
            threading.Thread(target=collector.collect,daemon=True).start()
            return self.respond({"status":"started"},202)

        def log_message(self,fmt,*values):
            # Request query strings may contain credentials; do not log them.
            return
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    threading.Thread(target=collector.schedule,daemon=True).start()
    print(f"Dashboard: http://{args.host}:{args.port}/perm-metal-prices-demo/#deals",flush=True)
    try:server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:pass
    finally:collector.stop.set();server.server_close()


def main():
    load_env(ROOT/".env.radar")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["once","serve"])
    parser.add_argument("--config",type=Path,default=ROOT/"radar.config.json")
    parser.add_argument("--snapshot",type=Path,default=ROOT/"public/data/snapshot.json")
    parser.add_argument("--output",type=Path,default=ROOT/"public/data/deals.json")
    parser.add_argument("--state",type=Path,default=ROOT/"state")
    parser.add_argument("--skip-prices",action="store_true")
    parser.add_argument("--scheduler",default="manual",choices=["manual","github_actions"])
    parser.add_argument("--web-dir",type=Path,default=ROOT.parent/"perm-metal-fallback/dist")
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=8787)
    args=parser.parse_args()
    if args.command=="once":Collector(args).collect()
    else:serve(args)


if __name__=="__main__":main()
