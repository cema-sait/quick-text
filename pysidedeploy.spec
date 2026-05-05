[app]
title = Quick Text
project_dir = /Users/paulkorir/PycharmProjects/quick-text
input_file = quick_text.py
exec_directory = /Users/paulkorir/PycharmProjects/quick-text/dist
project_file = 
icon = /Users/paulkorir/PycharmProjects/quick-text/paper_md_extractor/assets/app-icon.icns

[python]
python_path = /Users/paulkorir/PycharmProjects/quick-text/.venv/bin/python
packages = Nuitka==2.7.11
android_packages = buildozer==1.5.0,cython==0.29.33

[qt]
qml_files = 
excluded_qml_plugins = 
modules = Core,DBus,Gui,Svg,Widgets
plugins = accessiblebridge,iconengines,imageformats,platforms,platforms/darwin,platformthemes,styles

[android]
wheel_pyside = 
wheel_shiboken = 
plugins = 

[nuitka]
macos.permissions = 
mode = standalone
extra_args = --quiet --lto=no --nofollow-import-to=pymupdf --nofollow-import-to=fitz --include-module=ntpath --include-module=glob --noinclude-qt-translations --include-data-dir=/Users/paulkorir/PycharmProjects/quick-text/paper_md_extractor/assets=paper_md_extractor/assets --include-data-files=/Users/paulkorir/PycharmProjects/quick-text/.venv/lib/python3.13/site-packages/fitz/*.py=fitz/ --include-data-files=/Users/paulkorir/PycharmProjects/quick-text/.venv/lib/python3.13/site-packages/pymupdf/*.py=pymupdf/ --include-data-files=/Users/paulkorir/PycharmProjects/quick-text/.venv/lib/python3.13/site-packages/pymupdf/*.so=pymupdf/ --include-data-files=/Users/paulkorir/PycharmProjects/quick-text/.venv/lib/python3.13/site-packages/pymupdf/*.dylib=pymupdf/

[buildozer]
mode = debug
recipe_dir = 
jars_dir = 
ndk_path = 
local_libs = 
arch = 

