#!/usr/bin/env python3
"""Print what a dataset archive holds: files per folder and type, any readme or licence
text, and a sample label. Used once per new dataset before writing its adapter."""
import collections
import sys
import zipfile

archive = zipfile.ZipFile(sys.argv[1])
infos = [info for info in archive.infolist() if not info.is_dir()]
counts = collections.Counter(("/".join(info.filename.split("/")[:-1]),
                              info.filename.rsplit(".", 1)[-1].lower()) for info in infos)
for key, value in sorted(counts.items())[:80]:
    print("  ", key, value)
print("   files:", len(infos))
texts = [info for info in infos
         if info.filename.lower().rsplit("/", 1)[-1].startswith(("readme", "license", "licence", "data.yaml", "classes", "notes"))
         or info.filename.lower().endswith((".yaml", ".yml", ".md"))]
for info in texts[:6]:
    print("   ----", info.filename)
    print("    " + archive.read(info).decode("utf-8", "replace")[:1800].replace("\n", "\n    "))
labels = [info for info in infos if info.filename.lower().endswith((".txt", ".xml", ".json", ".csv"))
          and info not in texts]
for info in labels[:2] + labels[len(labels) // 2:len(labels) // 2 + 1]:
    print("   label", info.filename, repr(archive.read(info).decode("utf-8", "replace")[:300]))
images = [info for info in infos if info.filename.lower().endswith((".jpg", ".jpeg", ".png"))]
if images:
    from PIL import Image
    sizes = collections.Counter()
    for info in images[:: max(1, len(images) // 40)]:
        sizes[Image.open(archive.open(info)).size] += 1
    print("   image sizes (sample):", sizes.most_common(6), "first:", images[0].filename)
