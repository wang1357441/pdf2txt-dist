# -*- coding: utf-8 -*-
"""生成程序图标 app.ico（深底 + 蓝调，画一个 PDF→TXT 的小标）。"""
from PIL import Image, ImageDraw


def rounded(draw, box, r, fill):
    draw.rounded_rectangle(box, radius=r, fill=fill)


def main():
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 圆角底板（深蓝灰）
    rounded(d, (16, 16, 240, 240), 48, (20, 24, 32, 255))
    # 中间的蓝色文档块
    rounded(d, (54, 50, 202, 198), 22, (76, 141, 255, 255))
    # 文档里的白线（文字）
    for i, y in enumerate(range(84, 168, 22)):
        w = 110 if i % 2 == 0 else 78
        d.rounded_rectangle((74, y, 74 + w, y + 8), radius=4, fill=(255, 255, 255, 255))
    # 绿色箭头 →
    d.line([(150, 224), (196, 224)], fill=(61, 220, 132, 255), width=10)
    d.polygon([(184, 212), (200, 224), (184, 236)], fill=(61, 220, 132, 255))
    # TXT 字样
    d.rounded_rectangle((150, 196, 196, 250), radius=10, fill=(16, 18, 22, 255))
    d.line([(160, 214), (186, 214)], fill=(61, 220, 132, 255), width=7)
    d.line([(160, 230), (186, 230)], fill=(61, 220, 132, 255), width=7)
    d.line([(160, 246), (178, 246)], fill=(61, 220, 132, 255), width=7)

    img.save("app.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    # 同时导出一张 PNG，给 Linux / 统信 UOS 用（那些系统不认 .ico，要用 iconphoto 加载 PNG）
    img.save("app.png")
    print("saved app.ico / app.png", img.size)


if __name__ == "__main__":
    main()
