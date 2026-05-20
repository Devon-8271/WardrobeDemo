"""
快速测试 image2 生图服务。
用法：
  python test_image2.py                        # 纯文生图
  python test_image2.py photo.jpg              # 图+文
  python test_image2.py photo.jpg outfit.jpg   # 双图（试穿）
"""
import sys
import time
from image2_client import healthz, generate

PROMPT = "一件简洁的白色宽松T恤平铺在纯白背景上，商品图风格，高清"

def main():
    image_paths = sys.argv[1:] or []

    print("检查 OpenAI API...")
    if not healthz():
        print("OpenAI API 不可用，请检查 OPENAI_API_KEY 和网络连接")
        return

    print(f"API 在线，开始生图")
    print(f"Prompt: {PROMPT}")
    if image_paths:
        print(f"参考图: {image_paths}")

    t0 = time.time()
    out = generate(PROMPT, image_paths or None, out_dir="images", prefix="test")
    print(f"完成，耗时 {time.time()-t0:.0f}s，输出: {out}")

if __name__ == "__main__":
    main()
