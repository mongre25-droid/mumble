from PIL import Image
import os

img_path = "MacMumble/app/assets/mumble.png"
out_path = "MacMumble/app/assets/mumble.icns"

if os.path.exists(img_path):
    img = Image.open(img_path)
    img.save(out_path)
    print(f"Created {out_path}")
else:
    print(f"Error: {img_path} not found")
