# tests/test_handler.py
from dotenv import load_dotenv
load_dotenv()

from infrapilot.lambda_.handler import handler

def test_handler():
    result = handler({}, {})
    print(f"\n결과: {result}")
    assert result["status"] == "ok"

if __name__ == "__main__":
    test_handler()
    print("핸들러 테스트 완료!")