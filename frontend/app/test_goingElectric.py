import httpx
import asyncio
import os

API_URL = "https://api.goingelectric.de/chargepoints/"
API_KEY = os.getenv("GOINGELECTRIC_API_KEY")

async def test_goingelectric_api():
    if not API_KEY:
        raise RuntimeError("Set GOINGELECTRIC_API_KEY in the environment; never commit it")
    params = {
        "key": API_KEY,
        "lat": 49.4521,
        "lng": 11.0767,
        "radius": 5,
        "orderby": "distance"
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(API_URL, params=params)
            print(f"HTTP Status: {response.status_code}")
            
            data = response.json()
            if data.get("status") == "ok":
                stations = data.get("chargestations", [])
                print(f"✅ API 可用！成功拉取到 {len(stations)} 个充电站数据。")
                if stations:
                    first = stations[0]
                    print(f"示例站点: {first.get('name')} - 坐标: {first.get('coordinates')}")
            else:
                print(f"❌ 业务层报错: {data.get('error')}")
        except httpx.RequestError as exc:
            print(f"❌ 网络层异常: {exc}")

if __name__ == "__main__":
    asyncio.run(test_goingelectric_api())
