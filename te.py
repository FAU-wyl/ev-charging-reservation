
import json
import getpass
import urllib.request
import urllib.parse
import urllib.error

# GoingElectric API
API_URL = "https://api.goingelectric.de/chargepoints/"

# 输入你的 API Key
API_KEY = getpass.getpass("请输入 GoingElectric API Key: ").strip()

# 慕尼黑市中心附近 3 公里的充电站
params = {
    "key": API_KEY,
    "lat": 48.1374,
    "lng": 11.5755,
    "radius": 3,
}

url = API_URL + "?" + urllib.parse.urlencode(params)

try:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)

    print("API 状态:", data.get("status"))

    # 保存完整返回内容
    filename = "goingelectric_response.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    stations = data.get("chargelocations", [])

    print("本次返回充电站数量:", len(stations))
    print("完整数据已保存到:", filename)

    # 打印前 3 个充电站的完整 JSON
    print("\n========== 前 3 个充电站 ==========")
    print(json.dumps(
        stations[:3],
        indent=2,
        ensure_ascii=False
    ))

    # 统计前 10 个充电站
    print("\n========== 充电站摘要 ==========")
    for station in stations[:10]:
        print("-----------------------")
        print("名称:", station.get("name"))
        print("ID:", station.get("ge_id"))
        print("地址:", station.get("address"))
        print("坐标:", station.get("coordinates"))
        print("接口:", station.get("chargepoints"))
        print("网络:", station.get("network"))
        print("费用:", station.get("cost"))
        print("详情:", station.get("url"))

except urllib.error.HTTPError as e:
    print("HTTP 错误:", e.code)
    print(e.read().decode("utf-8", errors="replace")[:2000])

except urllib.error.URLError as e:
    print("网络错误:", e.reason)

except json.JSONDecodeError:
    print("服务器没有返回有效的 JSON")
