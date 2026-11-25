# завантажує 18 книг Project Gutenberg (добірка NLTK) у data/gutenberg/
import io
import urllib.request
import zipfile

URL = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/corpora/gutenberg.zip"

data = urllib.request.urlopen(URL).read()
with zipfile.ZipFile(io.BytesIO(data)) as zf:
    for name in zf.namelist():
        if name.endswith(".txt"):
            zf.extract(name, "data")
print("done")
