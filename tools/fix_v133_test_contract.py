from pathlib import Path

path = Path("tests/test_main_contract.py")
text = path.read_text(encoding="utf-8")
old = '    assert "原帖摘要（发言人 楼主 (UID author)）" in compressed\n'
new = (
    '    assert "原帖摘要（发言人 楼主 (UID author)）" not in compressed\n'
    '    assert "本轮省略原帖文字和原帖图片摘要" in compressed\n'
)
if text.count(old) != 1:
    raise SystemExit(f"expected one legacy drift assertion, got {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
