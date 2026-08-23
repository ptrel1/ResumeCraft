"""硅基流动 (SiliconFlow) 真实多模型账单与 Token 消耗连接器（支持动态时间戳范围与全量月份聚合）。"""
from __future__ import annotations

import calendar
import datetime
import json
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Tuple


class SiliconFlowConnector:
    name = "siliconflow"

    @staticmethod
    def get_month_timestamps(year: int, month: int) -> Tuple[int, int]:
        """获取指定月份第一天 00:00:00 到最后一秒的毫秒时间戳。"""
        _, last_day = calendar.monthrange(year, month)
        st = datetime.datetime(year, month, 1, 0, 0, 0)
        et = datetime.datetime(year, month, last_day, 23, 59, 59, 999000)
        return int(st.timestamp() * 1000), int(et.timestamp() * 1000)

    def fetch(self, config: Dict[str, Any]) -> Dict[str, Any]:
        api_key = config.get("api_key", "")
        cookie = config.get("cookie", "")
        auth_header = config.get("authorization", "")

        metrics = {
            "total_tokens": None,
            "total_tokens_str": "",
            "total_cost": None,
            "total_cost_str": "",
            "key_count": 0,
            "channels": [],
            "balance": None,
            "status": "no_live_data",
        }

        # 1. 尝试通过官方标准接口获取基础信息
        if api_key:
            try:
                url = "https://api.siliconflow.cn/v1/user/info"
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Authorization": f"Bearer {api_key}"
                    }
                )
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data.get("code") == 20000:
                            u_data = data.get("data", {})
                            metrics["balance"] = float(u_data.get("balance", 0))
                            metrics["total_balance"] = float(u_data.get("totalBalance", 0))
                            metrics["status"] = "active"
            except Exception:
                pass

        # 2. 如果配置了控制台 cookie 或 authorization，按自然月时间戳区间自动拉取真实详单
        if cookie or auth_header:
            try:
                today = datetime.date.today()
                # 自动计算当月时间戳区间（精确对齐官方 startTime 和 endTime）
                start_ms, end_ms = self.get_month_timestamps(today.year, today.month)
                bill_url = f"https://cloud.siliconflow.cn/panel-server/api/v1/bill/aggregate_amount?endTime={end_ms}&startTime={start_ms}"
                
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://cloud.siliconflow.cn/me/bills"
                }
                if auth_header:
                    headers["Authorization"] = auth_header
                if cookie:
                    headers["Cookie"] = cookie

                req = urllib.request.Request(bill_url, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        res_json = json.loads(resp.read().decode("utf-8"))
                        if res_json.get("code") == 20000:
                            items = res_json.get("data", {}).get("list", [])
                            tot_k = sum(float(item.get("grossUsage", 0)) for item in items)
                            tot_cost = sum(float(item.get("deductAmount", 0)) for item in items)
                            if tot_k > 0:
                                metrics["total_tokens"] = int(tot_k * 1000)
                                tokens_m = tot_k / 1000
                                metrics["total_tokens_str"] = f"{tokens_m/1000:.2f} 亿 Tokens" if tokens_m >= 1000 else f"{tokens_m:.1f} 万 Tokens"
                                metrics["total_cost"] = round(tot_cost, 2)
                                metrics["total_cost_str"] = f"{tot_cost:.2f} 元"
                                metrics["key_count"] = len(items)
                                # 提取渠道说明
                                desc_list = [item.get("apiKeyDesc") for item in items if item.get("apiKeyDesc")]
                                if desc_list:
                                    metrics["channels"] = desc_list
            except Exception:
                pass

        return metrics
