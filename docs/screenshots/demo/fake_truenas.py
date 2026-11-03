"""A TrueNAS DDP endpoint with fixed answers, for screenshots. wss on 8443 with a self-signed cert."""
import asyncio, json, ssl, sys, time
from websockets.asyncio.server import serve

DAY = 86400_000
now_ms = int(time.time() * 1000)
POOLS = [
    {"name": "tank", "status": "ONLINE", "healthy": True, "size": 15_942_918_602_752, "allocated": 9_724_180_348_928,
     "free": 6_218_738_253_824, "scan": {"state": "FINISHED", "end_time": {"$date": now_ms - 9 * DAY}, "errors": 0}},
    {"name": "vault", "status": "ONLINE", "healthy": True, "size": 15_942_918_602_752, "allocated": 12_595_905_696_563,
     "free": 3_347_012_906_189, "scan": {"state": "FINISHED", "end_time": {"$date": now_ms - 8 * DAY}, "errors": 0}},
]
ALERTS = [
    {"level": "WARNING", "formatted": "SMART: Device /dev/sdd, 2 Currently unreadable (pending) sectors.",
     "datetime": {"$date": now_ms - 2 * DAY}, "dismissed": False},
]
# The root datasets: `available` is the pool's free space minus ZFS's 128 GiB slop space
DATASETS = [{"id": p["name"], "type": "FILESYSTEM", "name": p["name"], "pool": p["name"],
             "available": {"parsed": p["free"] - 128 * 1024**3, "rawvalue": str(p["free"] - 128 * 1024**3),
                           "value": f"{(p['free'] - 128 * 1024**3) / 1024**4:.2f} TiB", "source": "NONE",
                           "source_info": None}}
            for p in POOLS]
ANSWERS = {"auth.login_with_api_key": True, "alert.list": ALERTS, "pool.query": POOLS,
           "pool.dataset.query": DATASETS}

async def handler(ws):
    async for raw in ws:
        msg = json.loads(raw)
        if msg.get("msg") == "connect":
            await ws.send(json.dumps({"msg": "connected", "session": "demo"}))
        elif msg.get("msg") == "method":
            await ws.send(json.dumps({"msg": "result", "id": msg["id"], "result": ANSWERS.get(msg["method"])}))

async def main():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(sys.argv[1], sys.argv[2])
    async with serve(handler, "0.0.0.0", 8443, ssl=ctx):
        await asyncio.Future()

asyncio.run(main())
