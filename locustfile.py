"""Locust 压测脚本（规格 12：报告 QPS / P50 / P99，并复现 503 + Retry-After）。

运行：
    locust -f locustfile.py --host http://localhost:8000 -u 50 -r 10 -t 2m --headless -p 8089

覆盖两条主链路：
    1) POST /tickets          建单（触发护栏：限流/配额/熔断，命中即 429/503）
    2) GET  /tickets/{id}/trace  轮询轨迹（前端 §11 的轮询行为）

过载复现：收到 503（全局熔断）时读取 Retry-After 头并按其退避，避免无意义重试放大雪崩；
429（限流/配额）同样读取 Retry-After。这样压测报告的 P50/P99 才反映真实降级链路，
而非客户端疯狂重试造成的假象。
"""
import os
import time

from locust import HttpUser, between, events, task

# 演示端固定 token（规格 1.2 不做注册登录）；可用环境变量覆盖
TOKEN = os.getenv("DEMO_TOKEN", "dev-token")

# 三语种样例，贴近真实多语种流量
SAMPLES = [
    ("en", "Where is my order? The tracking shows no update for a week."),
    ("es", "Quiero devolver este producto, ¿cuál es la política de devolución?"),
    ("id", "Kapan pesanan saya tiba? Nomor resi saya SF1234567890."),
]


class SupportUser(HttpUser):
    # 建单快、轮询慢，贴近真实节奏
    wait_time = between(1, 3)

    def on_start(self):
        self._idx = 0

    def _auth(self):
        return {"Authorization": f"Bearer {TOKEN}"}

    @task(3)
    def create_ticket(self):
        lang, text = SAMPLES[self._idx % len(SAMPLES)]
        self._idx += 1
        with self.client.post(
            "/tickets",
            json={"text": text, "lang": lang},
            headers=self._auth(),
            catch_response=True,
            name="POST /tickets",
        ) as resp:
            # 200 = 受理；429/503 是护栏的有意降级，记为失败但带回退，不计入成功 QPS
            if resp.status_code == 200:
                try:
                    self._last_id = resp.json().get("ticket_id")
                except Exception:
                    self._last_id = None
                resp.success()
            elif resp.status_code in (429, 503):
                # 复现过载：读取 Retry-After 并按其退避
                retry = resp.headers.get("Retry-After")
                if retry and retry.isdigit():
                    time.sleep(min(int(retry), 5))  # 上限 5s，避免压测端卡死
                resp.failure(f"throttled:{resp.status_code}")
            else:
                resp.failure(f"unexpected:{resp.status_code}")

    @task(1)
    def poll_trace(self):
        tid = getattr(self, "_last_id", None)
        if tid is None:
            return
        with self.client.get(
            f"/tickets/{tid}/trace",
            headers=self._auth(),
            catch_response=True,
            name="GET /tickets/{id}/trace",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code in (429, 503):
                retry = resp.headers.get("Retry-After")
                if retry and retry.isdigit():
                    time.sleep(min(int(retry), 5))
                resp.failure(f"throttled:{resp.status_code}")
            else:
                resp.failure(f"unexpected:{resp.status_code}")


@events.quitting.add_listener
def _on_quit(environment, **_kwargs):
    # locust 结束时在日志里提示：P50/P99 见 Web UI 或 --csv 导出
    environment.stats  # noqa: B018 — 触发惰性统计收集
    print("[locust] 压测结束：QPS/P50/P99 见 Web UI 或 --csv 导出。")
