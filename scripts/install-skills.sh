#!/usr/bin/env sh
# Copy the skills to the user level, with no dependency on anything working.
#
# `career-evidence install-skills` does the same thing, but it lives inside the
# Python package -- so if the package fails to install, you cannot install the
# skills either. That dependency should never have existed: this is a file
# copy. When the packaging breaks, this still works.
set -e

src="$(cd "$(dirname "$0")/.." && pwd)/skills"
dest="${HOME}/.claude/skills"

[ -d "$src" ] || { echo "找不到技能目录：$src"; exit 1; }
mkdir -p "$dest"

for skill in "$src"/career-evidence*; do
    [ -f "$skill/SKILL.md" ] || continue
    name="$(basename "$skill")"
    rm -rf "$dest/$name"
    cp -r "$skill" "$dest/$name"
    echo "  装好  $name"
done

echo ""
echo "位置：$dest"
command -v career-evidence >/dev/null 2>&1 \
    && echo "开新会话后，任意目录下 /career-evidence 都能用了。" \
    || echo "注意：career-evidence 命令还不在 PATH 上（技能会调它）——在仓库目录里 pip install -e ."
