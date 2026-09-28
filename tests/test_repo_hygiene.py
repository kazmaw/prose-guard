"""公開 repo に、個人の環境を示す文字列が入っていないことを確かめる。

社名などの禁止語はここに書かない。一覧そのものが固有情報になるため、push 前に手で確かめる。
"""

import subprocess
import unittest

from helpers import ROOT

# 文字列を割って書き、このファイル自身が引っ掛からないようにする
FORBIDDEN = ("/Us" + "ers/", "Mobile" + " Documents", "iCloud~md~" + "obsidian")


def repo_files():
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=str(ROOT), capture_output=True, check=True).stdout
    return [ROOT / p for p in out.decode("utf-8").split("\0") if p]


class HygieneTest(unittest.TestCase):
    def test_no_personal_paths(self):
        hits = []
        for p in repo_files():
            if not p.is_file():
                continue
            text = p.read_bytes().decode("utf-8", errors="ignore")
            hits += ["%s: %s" % (p.relative_to(ROOT), w) for w in FORBIDDEN if w in text]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
