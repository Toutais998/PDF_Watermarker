from PIL import Image, ImageDraw, ImageFont

def create_pdf_icon():
    # 创建一个 256x256 的透明背景画布
    img = Image.new('RGBA', (256, 256), color=(255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    # 绘制一个圆角红色底框，经典的 PDF 配色
    draw.rounded_rectangle([20, 20, 236, 236], radius=40, fill="#E2574C")

    # 绘制中间的白色文档纸张
    draw.rectangle([60, 50, 196, 206], fill="white")
    
    # 绘制右上角的折角视觉效果
    draw.polygon([(156, 50), (196, 50), (196, 90)], fill="#E2574C") 
    draw.polygon([(156, 50), (196, 90), (156, 90)], fill="#DCDCDC")

    # 尝试调用系统自带的微软雅黑或 Arial 粗体写入 PDF 字样
    try:
        font = ImageFont.truetype("msyhbd.ttc", 40)
    except IOError:
        try:
            font = ImageFont.truetype("arialbd.ttf", 46)
        except IOError:
            font = ImageFont.load_default()

    # 将文字写入文档中间
    draw.text((75, 120), "PDF", fill="#E2574C", font=font)
    
    # 补充两条装饰性的文本横线
    draw.rectangle([75, 175, 175, 183], fill="#E2574C")
    draw.rectangle([75, 190, 145, 198], fill="#E2574C")

    # 导出为包含多种标准尺寸的 Windows ICO 文件
    img.save('pdf_tool_icon.ico', format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (32, 32)])
    print("桌面图标 pdf_tool_icon.ico 已成功生成！")

if __name__ == "__main__":
    create_pdf_icon()