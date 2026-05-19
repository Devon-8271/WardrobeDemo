"""
快速测试 OpenAI gpt-image-2 生图。
"""
import base64, os
from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

result = client.images.generate(
    model="gpt-image-2",
    prompt="a white t-shirt laid flat on a white background, product photo style",
    size="1024x1024",
    n=1,
)

img_bytes = base64.b64decode(result.data[0].b64_json)
with open("output.png", "wb") as f:
    f.write(img_bytes)

print("生成完成，保存为 output.png")
