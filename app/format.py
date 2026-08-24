import logging
import re

from telegram import Message
from telegram.constants import ParseMode
from telegram.error import BadRequest

logger = logging.getLogger(__name__)


def _escape_mdv2(text: str) -> str:
    """Escape MarkdownV2 special chars in plain text segment. Idempotent: don't double-escape."""
    out = []
    specials = set(r'_*[]()~`>#+-=|{}.!\\')
    for i, ch in enumerate(text):
        if ch in specials:
            if i > 0 and text[i - 1] == '\\':
                out.append(ch)  # already escaped
            else:
                out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


def _strip_headings(text: str) -> str:
    """Convert ATX headings (## Title) to bold. Must run before inline conversion."""
    def repl(m):
        inner = m.group(2).strip()
        if not inner:
            return ""
        # Unwrap existing markdown wrappers inside heading to avoid **** nesting
        # e.g. ## **Bold** -> *Bold*, not ****
        for pat in (r'^\*\*(.+)\*\*$', r'^__(.+)__$', r'^\*(.+)\*$', r'^_(.+)_$', r'^~~(.+)~~$'):
            mm = re.match(pat, inner, flags=re.DOTALL)
            if mm:
                inner = mm.group(1)
                break
        return f"**{inner}**" if inner else ""
    return re.sub(r'^[ \t]{0,3}(#{1,6})\s+(.+?)\s*(?:#+\s*)?$', repl, text, flags=re.MULTILINE)


def markdown_to_mdv2(text: str) -> str:
    """Convert common Markdown (as produced by LLMs) to Telegram MarkdownV2.

    - Strips ATX headings ## -> bold, preserves ```code blocks```, `inline code`, [links](url).
    - Converts **bold** -> *bold*, __bold__ -> *bold*, *italic* / _italic_ -> _italic_, ~~strike~~ -> ~strike~
    - Escapes remaining MarkdownV2 specials outside code/link zones.
    - If input already looks like MarkdownV2 (contains \\* etc.), leaves escapes intact.
    """
    if not text:
        return text
    text = _strip_headings(text)
    # Protect code blocks and inline code — never touch inside
    # Split by ```...``` and `...`
    code_re = re.compile(r'(```.*?```|`[^`]*?`)', re.DOTALL)
    parts = code_re.split(text)
    out_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # code
            out_parts.append(part)
            continue
        # Protect links [text](url) — escape link text, leave url as-is
        link_re = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
        segs: list[str] = []
        last = 0
        for m in link_re.finditer(part):
            before = part[last:m.start()]
            segs.append(_convert_inline(before))
            # link text needs escaping inside [] but not double-escape
            link_text = _escape_mdv2(m.group(1))
            segs.append(f'[{link_text}]({m.group(2)})')
            last = m.end()
        segs.append(_convert_inline(part[last:]))
        out_parts.append(''.join(segs))
    return ''.join(out_parts)


def _convert_inline(text: str) -> str:
    # Order matters: process longest delimiters first
    # Temporarily replace markers with placeholders to avoid re-escaping
    placeholders: dict[str, str] = {}

    def _ph(key: str, inner: str, mdv2_wrap: str) -> str:
        # inner is already escaped
        ph = f'\x00{len(placeholders)}\x00'
        placeholders[ph] = f'{mdv2_wrap[0]}{inner}{mdv2_wrap[1]}' if len(mdv2_wrap) == 2 else f'{mdv2_wrap}{inner}{mdv2_wrap}'
        return ph

    # Escape first, then un-escape bold/italic wrappers via placeholder logic on raw text
    # So we operate on raw text, then escape inner.
    # 1) **bold** -> *bold*
    def repl_bold_star(m):
        inner = _escape_mdv2(m.group(1))
        return _ph('b', inner, '*')
    text = re.sub(r'\*\*(.+?)\*\*', repl_bold_star, text)
    # 2) __bold__ -> *bold*
    def repl_bold_under(m):
        inner = _escape_mdv2(m.group(1))
        return _ph('b', inner, '*')
    text = re.sub(r'__(.+?)__', repl_bold_under, text)
    # 3) ~~strike~~ -> ~strike~
    def repl_strike(m):
        inner = _escape_mdv2(m.group(1))
        return _ph('s', inner, '~')
    text = re.sub(r'~~(.+?)~~', repl_strike, text)
    # 4) *italic* -> _italic_  (avoid ** already handled, so remaining single *)
    def repl_italic_star(m):
        inner = _escape_mdv2(m.group(1))
        return _ph('i', inner, '_')
    # need to avoid matching already replaced placeholders (\x00)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', repl_italic_star, text)
    # 5) _italic_ -> _italic_ (single underscore)
    def repl_italic_under(m):
        inner = _escape_mdv2(m.group(1))
        return _ph('i', inner, '_')
    text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', repl_italic_under, text)

    # Now escape whatever is left (not inside placeholders)
    # Split by placeholders
    if placeholders:
        # build regex for placeholders
        ph_re = re.compile('(' + '|'.join(re.escape(k) for k in placeholders) + ')')
        split = ph_re.split(text)
        out = []
        for seg in split:
            if seg in placeholders:
                out.append(placeholders[seg])
            else:
                out.append(_escape_mdv2(seg))
        text = ''.join(out)
    else:
        text = _escape_mdv2(text)
    return text


# Deprecated alias for backwards compat (was buggy - escaped everything including formatting)
def _escape_outside_code(text: str) -> str:
    return markdown_to_mdv2(text)

# Split long text into chunks <= limit, preferring paragraph/line breaks
def split_text(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # try split at \n\n, then \n, then space
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind(" ", 0, limit)
        if cut == -1:
            cut = limit
        else:
            cut += 1  # keep delimiter
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
        if not remaining:
            break
    if remaining:
        chunks.append(remaining)
    return chunks


async def safe_reply_text(message: Message, text: str, parse_mode: str | None = ParseMode.MARKDOWN_V2) -> Message:
    """Send reply trying MarkdownV2, fallback to plain text on BadRequest."""
    text = text or "—"
    if parse_mode == ParseMode.MARKDOWN_V2:
        text = markdown_to_mdv2(text)
    chunks = split_text(text[:12000])  # hard cap to avoid flooding
    first_msg: Message | None = None
    for idx, chunk in enumerate(chunks):
        target = message if idx == 0 else first_msg  # type: ignore
        # For subsequent chunks, send as new message via reply
        try:
            if parse_mode:
                if idx == 0:
                    first_msg = await message.reply_text(chunk, parse_mode=parse_mode)
                else:
                    await message.reply_text(chunk, parse_mode=parse_mode)
            else:
                if idx == 0:
                    first_msg = await message.reply_text(chunk)
                else:
                    await message.reply_text(chunk)
        except BadRequest as e:
            msg = str(e).lower()
            if "can't parse" in msg or "parse" in msg:
                logger.warning("markdown parse failed, fallback plain: %s", e)
                if idx == 0:
                    first_msg = await message.reply_text(chunk)
                else:
                    await message.reply_text(chunk)
            else:
                raise
    return first_msg  # type: ignore[return-value]


async def safe_edit_text(message: Message, text: str, parse_mode: str | None = ParseMode.MARKDOWN_V2) -> None:
    """Edit message trying MarkdownV2, fallback to plain text."""
    text = text or "—"
    if parse_mode == ParseMode.MARKDOWN_V2:
        text = markdown_to_mdv2(text)
    # edit only first chunk (Telegram edit can't split); truncate
    chunk = text[:4000]
    try:
        if parse_mode:
            await message.edit_text(chunk, parse_mode=parse_mode)
        else:
            await message.edit_text(chunk)
    except BadRequest as e:
        msg = str(e).lower()
        if "can't parse" in msg or "parse" in msg or "message is not modified" in msg:
            if "not modified" in msg:
                return
            logger.warning("markdown edit parse failed, fallback plain: %s", e)
            try:
                await message.edit_text(chunk)
            except BadRequest as e2:
                if "not modified" not in str(e2).lower():
                    raise
        else:
            raise


async def safe_send_or_edit(status_msg: Message, text: str, is_edit: bool = True) -> None:
    """Helper for handlers that have a placeholder status message."""
    if is_edit:
        await safe_edit_text(status_msg, text)
    else:
        await safe_reply_text(status_msg, text)
