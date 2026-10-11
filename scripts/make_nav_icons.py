"""从官方 logo 提取图标部分，生成方形导航图标 SVG。"""
import re

# Vultr：提取 <g id="sygnet"> 图标部分
vultr = open("assets/vultr_logo.svg").read()
m = re.search(r'<g id="sygnet">(.*?)</g>', vultr, re.S)
assert m, "找不到 Vultr sygnet"
sygnet = m.group(1)
pairs = re.findall(r'([0-9.]+),([0-9.]+)', sygnet)
max_x = max(float(a) for a, b in pairs)
max_y = max(float(b) for a, b in pairs)
w = int(max_x) + 6
h = int(max_y) + 6
defs = re.search(r'<defs>.*?</defs>', vultr, re.S).group(0)
vultr_icon = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">{defs}{sygnet}</svg>'
open("assets/vultr_icon.svg", "w").write(vultr_icon)
print(f"Vultr 图标: {w}x{h}")
