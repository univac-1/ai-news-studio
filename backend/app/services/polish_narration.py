import json
import re

import vertexai
from vertexai.generative_models import GenerativeModel

from ..core.config import settings
from ..schemas.draft import SegmentVisual, VideoPlanDraft
from .categorize import category_style
from .generate_weekly_video_plan import (
    ACTION_MAX_CHARS,
    HOOK_MAX_CHARS,
    IMPACT_MAX_CHARS,
    OPENING_MAX_CHARS,
    OUTRO_MAX_CHARS,
    RANK_REASON_MAX_CHARS,
    SUMMARY_MAX_CHARS,
    TITLE_JA_MAX_CHARS,
    contains_japanese,
    template_hook,
    template_intro_line,
    template_reaction_line,
)

# ずんだもんの一言導入(intro_line)は30〜40字程度を目安にするが、
# Gemini出力のブレを許容するため採用判定はやや広めの上限にする
ZUNDAMON_LINE_MAX_CHARS = 40
# ずんだもんの一言感想(reaction_line)は20〜30字程度を目安にするが、
# Gemini出力のブレを許容するため採用判定はやや広めの上限にする
ZUNDAMON_REACTION_MAX_CHARS = 30
SPOKEN_SPEAKER_LABELS = ("AI専門家",)


def sanitize_spoken_speaker_labels(text: str) -> str:
    """音声・字幕用テキストに混入した内部の話者ラベルを除去する。"""
    cleaned = text
    for label in SPOKEN_SPEAKER_LABELS:
        cleaned = re.sub(rf"{re.escape(label)}\s*([がはもをにへとで])", r"\1", cleaned)
        cleaned = re.sub(rf"{re.escape(label)}\s*の", "", cleaned)
        cleaned = cleaned.replace(label, "")
    cleaned = re.sub(r"([、。！？!?])\1+", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.replace("次にが", "次に").replace("続いてが", "続いて")
    return cleaned


def _parse_visual(raw: object) -> SegmentVisual | None:
    # Gemini出力のvisualは厳格に検証し、少しでも不正なら黙って捨てる(通常レイアウトで描画)
    if not isinstance(raw, dict):
        return None
    visual_type = raw.get("type")
    items = raw.get("items")
    if visual_type not in ("flow", "command") or not isinstance(items, list):
        return None
    cleaned = [item.strip() for item in items if isinstance(item, str) and item.strip()]
    if visual_type == "flow" and not (3 <= len(cleaned) <= 4):
        return None
    if visual_type == "command" and not (1 <= len(cleaned) <= 3):
        return None
    return SegmentVisual(type=visual_type, items=cleaned)


def _clean_thumbnail_text_candidate(raw: object) -> str | None:
    """「メイン｜サブ」形式の候補を検証し、描画用の「メイン\nサブ」形式に整える。

    メインはサムネに最大サイズで載せる2〜8文字のパワーワード。
    サブは補足の短い一言(任意)。サブが不正でもメインが有効なら採用する。
    """
    if not isinstance(raw, str):
        return None
    parts = [part.strip() for part in re.split(r"[｜|\n]", raw) if part.strip()]
    if not parts:
        return None
    main = re.sub(r"[、。，．！？!?「」『』【】\s]+", "", parts[0])
    if not main:
        return None
    sub = ""
    if len(parts) >= 2:
        sub = re.sub(r"[。，．「」『』【】]+", "", parts[1])
        sub = re.sub(r"\s+", " ", sub).strip()
    return f"{main}\n{sub}" if sub else main


async def polish_narration(draft: VideoPlanDraft) -> VideoPlanDraft:
    if not settings.GEMINI_PROJECT:
        return draft

    try:
        vertexai.init(project=settings.GEMINI_PROJECT, location=settings.GEMINI_LOCATION)
        model = GenerativeModel("gemini-2.5-flash")

        segments_text = "\n\n".join(
            f"### セグメント{seg.number}\n"
            f"見出し: {seg.headline}\n"
            f"カテゴリ: {category_style(seg.category).label}\n"
            f"要約: {seg.summary}\n"
            f"インパクト: {seg.impact}\n"
            f"アクション: {seg.action}\n"
            f"ナレーション: {seg.narration}"
            for seg in draft.segments
        )

        prompt = (
            f"以下のYouTube AIニュース動画ドラフトを、視聴維持率が上がる構成に書き換えてください。\n"
            f"視聴者はビジネスパーソン・開発者。信頼感を保ち、煽りすぎないこと。\n\n"
            "この動画は2話者(掛け合い)構成で、各ニュースは"
            "「ずんだもん(導入)→解説役(詳細解説)→ずんだもん(感想)」の3段構成です。\n"
            "・「ずんだもん」: 全体進行役。一人称は「ボク」、語尾に「〜のだ」「〜なのだ」を使う、明るく元気な口調。"
            "フック・オープニング・各ニュースの一言導入(zundamon_lines)・各ニュースの一言感想(zundamon_reactions)"
            "・まとめを担当。\n"
            "・「解説役」: 各ニュースの詳細解説役。一人称は「私」、です・ます調、時々「〜ですね」を使う、"
            "落ち着いたトーン。各ニュースの詳細解説(narrations)を担当。\n\n"
            "重要: 出力する台本本文には「AI専門家」「解説役」のような話者名・役名を書かないこと。"
            "誰が話すかはシステム側で制御するため、本文には発話内容だけを書くこと。\n\n"
            f"動画タイトル: {draft.title}\n\n"
            f"【現在のフック】\n{draft.hook}\n\n"
            f"【現在のオープニング】\n{draft.intro}\n\n"
            f"【セグメント一覧】\n{segments_text}\n\n"
            f"【現在のまとめ】\n{draft.outro}\n\n"
            "音声合成(VOICEVOX、約8〜9文字/秒)で読み上げるため、文字数指定は厳守してください。\n\n"
            "生成する項目:\n"
            f"1. hook: 動画冒頭0〜5秒。今週最大のニュースを一言で提示。{HOOK_MAX_CHARS}文字以内。\n"
            f"2. intro: 5〜20秒のオープニング原稿。前半で「この動画を見る価値(何本を何分で把握できるか)」を提示し、"
            f"後半は「ラインナップは画面のとおりです。それでは1本目からいきましょう」のように"
            f"テンポよく本編へつなぐ。{OPENING_MAX_CHARS}文字以内。ニュースタイトルの列挙はしない(画面に一覧が出る)。\n"
            "3. narrations: 各セグメントの詳細解説ナレーション。解説役の口調(一人称は「私」、"
            "です・ます調、時々「〜ですね」を使う、落ち着いたトーン)で書く。"
            "「インパクト:」「アクション:」のようなラベル読み上げをやめて内容を文章に織り込む。"
            "「つまり何が重要か」「誰に影響するか」「視聴者が次に何を押さえるべきか」が明確に伝わる構成にする。"
            "ただし事実の改変・誇張・断定的な予測は禁止。"
            "スライドに表示される文言(タイトル・要約・箇条書き)をそのまま復唱しない。"
            "スライドは「見せる」、ナレーションは「補足と実況」と役割分担する。"
            "セグメント間のつなぎ(「続いては〜」など)を入れる。事実・固有名詞・数値は変えない。"
            f"なお、要約は{SUMMARY_MAX_CHARS}文字以内、インパクトは{IMPACT_MAX_CHARS}文字以内、"
            f"アクションは{ACTION_MAX_CHARS}文字以内を目安に簡潔にまとめられている前提のため、"
            "織り込む際もこの分量感を保ち、冗長に膨らませないこと。"
            "敬語の誤用をしない(例:「〜を評価されることをお勧めします」のような受身と勧奨の"
            "混在は不可。「〜を評価するのがおすすめです」のように書く)。"
            "「〜したというものです」「〜となっています」のような間延びした言い回しは避ける。"
            "1文はおおむね50字以内で区切る。"
            "各セグメントの締めの文型は毎回変え、全ニュースが「ご検討ください」"
            "「お勧めします」のような同じ結び方で終わらないようにする。\n"
            "4. zundamon_lines: 各セグメントの一言導入。「ずんだもん」の口調"
            "(一人称は「ボク」、語尾に「〜のだ」「〜なのだ」、明るく元気)で、"
            f"そのニュースの内容を{ZUNDAMON_LINE_MAX_CHARS}文字以内(目安30〜40字)で一言だけ紹介する。"
            "詳細には踏み込まず、次の詳細解説への前フリとして機能する短い一文にする。"
            "narrationsと文体・視点が重複しないようにする。"
            "複数のモデル名・製品名を挙げる場合は「AとB」のように自然な日本語でつなぎ、"
            "「A, B」のような英語的なカンマ列挙はしない。\n"
            "5. zundamon_reactions: 各セグメントについて、詳細解説を受けてのずんだもんの一言感想。"
            "「ずんだもん」の口調(一人称は「ボク」、語尾に「〜のだ」「〜なのだ」、明るく元気)で、"
            f"{ZUNDAMON_REACTION_MAX_CHARS}文字以内(目安20〜30字)。"
            "zundamon_linesの内容を繰り返さず、解説内容への驚き・納得・ツッコミなどの反応にする。\n"
            "6. segments_meta: 各セグメントについて次の3つ。\n"
            f"   - title_ja: スライド表示用の短い日本語タイトル。{TITLE_JA_MAX_CHARS}文字以内。"
            "英語見出しは意味を保って日本語化する。誇張・事実改変は禁止。\n"
            "   - visual: 画面を補足する図解データ。該当する場合のみ。\n"
            '     セキュリティ系: {"type":"flow","items":["悪意あるWebサイト","AIブラウザ/LLM","ガードレール回避","情報・コード漏えい"]} '
            "のような3〜4ステップの攻撃フロー(各14文字以内)。\n"
            '     開発ツール系: {"type":"command","items":["コマンド例や利用イメージ(各40文字以内、最大3行)"]}。\n'
            "     どちらにも該当しない場合は null。無理に作らない。\n"
            f"   - rank_reason: このニュースがなぜ重要かの一言理由。{RANK_REASON_MAX_CHARS}文字以内。"
            "例: 政府・規制産業向けAI活用の本格化\n"
            "7. outro: まとめ原稿。「今週の重要度ランキング」として第1位〜第3位(セグメント1〜3がそのまま順位)を、"
            "各順位は短いタイトルと一言理由だけで振り返る(例: 1位は◯◯、政府向けAI活用の本格化。)。"
            f"最後にチャンネル登録・通知オンを短く促す。{OUTRO_MAX_CHARS}文字以内。\n"
            "8. title_candidates: YouTubeタイトル案を5つ。最重要ニュースの具体的な内容を軸に、"
            "数字・ベネフィット・意外性のいずれかを含める。40文字以内。"
            "釣りタイトル(内容と乖離した誇張)は禁止。"
            "日付範囲は入れない。\n"
            "9. thumbnail_text_candidates: サムネイル文言案を5つ。各案は「メイン｜サブ」形式の1つの文字列。\n"
            "   - メイン: サムネに最大サイズで載せる一言。2〜8文字、句読点なし。"
            "最重要ニュースの固有名詞・数字・意外性を軸にした具体的なパワーワードにする。"
            "「AI激変」「衝撃」のような抽象語だけの案は避ける。"
            "例: GPT-6来た｜…、Gemini無料化｜…、中国AI猛追｜…。\n"
            "   - サブ: メインの文脈を一言で補足する6〜14文字。視聴者への影響や意外性を足す。"
            "例: …｜開発者は今すぐ確認、…｜勢力図が一変、…｜性能2倍で半額。\n"
            "   5案のうち少なくとも3案はメインに固有名詞または数字を含めること。"
            "内容と乖離した誇張は禁止。\n\n"
            "出力はJSONのみ:\n"
            '{"hook": "...", "intro": "...", "narrations": ["...", ...], '
            '"zundamon_lines": ["...", ...], '
            '"zundamon_reactions": ["...", ...], '
            '"segments_meta": [{"title_ja": "...", "visual": {"type": "flow", "items": ["...", "...", "..."]}, '
            '"rank_reason": "..."}, ...], '
            '"outro": "...", "title_candidates": ["...", "...", "...", "...", "..."], '
            '"thumbnail_text_candidates": ["...", "...", "...", "...", "..."]}\n'
            f"narrations、zundamon_lines、zundamon_reactions、segments_metaはセグメントと同数・同順"
            f"({len(draft.segments)}件)で返してください。\n"
            "説明は不要です。JSONのみ返してください。"
        )

        response = await model.generate_content_async(prompt)
        text = response.text.strip()

        # Strip markdown code fences if present
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

        result = json.loads(text.strip())

        narrations: list[str] = result["narrations"]
        if len(narrations) != len(draft.segments):
            return draft

        # 採用時バリデーション: Geminiがバジェットを無視して超過した場合は元の値を維持する
        raw_intro = result["intro"]
        new_intro = (
            raw_intro
            if isinstance(raw_intro, str) and len(raw_intro) <= OPENING_MAX_CHARS + 15
            else draft.intro
        )
        raw_outro = result["outro"]
        new_outro = (
            raw_outro
            if isinstance(raw_outro, str) and len(raw_outro) <= OUTRO_MAX_CHARS
            else draft.outro
        )

        # segments_meta はフィールド単位で defensive に採用する
        raw_meta = result.get("segments_meta")
        meta_list = (
            raw_meta
            if isinstance(raw_meta, list) and len(raw_meta) == len(draft.segments)
            else [{} for _ in draft.segments]
        )

        # zundamon_lines も他のフィールドと同様にリスト長を検証し、一致しない場合は
        # 各セグメントとも下のフォールバック(template_intro_line)に倒す
        raw_zundamon_lines = result.get("zundamon_lines")
        zundamon_lines_list = (
            raw_zundamon_lines
            if isinstance(raw_zundamon_lines, list)
            and len(raw_zundamon_lines) == len(draft.segments)
            else [None for _ in draft.segments]
        )

        # zundamon_reactions も同様にリスト長を検証し、一致しない場合は
        # 各セグメントとも下のフォールバック(template_reaction_line)に倒す
        raw_zundamon_reactions = result.get("zundamon_reactions")
        zundamon_reactions_list = (
            raw_zundamon_reactions
            if isinstance(raw_zundamon_reactions, list)
            and len(raw_zundamon_reactions) == len(draft.segments)
            else [None for _ in draft.segments]
        )

        new_segments = []
        for i, seg in enumerate(draft.segments):
            meta = meta_list[i] if isinstance(meta_list[i], dict) else {}
            raw_title_ja = meta.get("title_ja")
            title_ja = (
                raw_title_ja.strip()
                if isinstance(raw_title_ja, str)
                and raw_title_ja.strip()
                and len(raw_title_ja.strip()) <= TITLE_JA_MAX_CHARS + 4
                and contains_japanese(raw_title_ja.strip())
                else seg.title_ja
            )
            visual = _parse_visual(meta.get("visual"))
            raw_rank_reason = meta.get("rank_reason")
            rank_reason = (
                raw_rank_reason.strip()
                if isinstance(raw_rank_reason, str)
                and raw_rank_reason.strip()
                and len(raw_rank_reason.strip()) <= RANK_REASON_MAX_CHARS
                else seg.rank_reason
            )
            raw_zundamon_line = zundamon_lines_list[i]
            intro_line = (
                sanitize_spoken_speaker_labels(raw_zundamon_line.strip())
                if isinstance(raw_zundamon_line, str)
                and raw_zundamon_line.strip()
                and len(raw_zundamon_line.strip()) <= ZUNDAMON_LINE_MAX_CHARS
                else template_intro_line(seg.number, title_ja)
            )
            raw_zundamon_reaction = zundamon_reactions_list[i]
            reaction_line = (
                sanitize_spoken_speaker_labels(raw_zundamon_reaction.strip())
                if isinstance(raw_zundamon_reaction, str)
                and raw_zundamon_reaction.strip()
                and len(raw_zundamon_reaction.strip()) <= ZUNDAMON_REACTION_MAX_CHARS
                else template_reaction_line(seg.number)
            )
            new_segments.append(
                seg.model_copy(
                    update={
                        "narration": sanitize_spoken_speaker_labels(narrations[i]),
                        "title_ja": title_ja,
                        "slide_title": f"#{seg.number} {title_ja}" if title_ja else seg.slide_title,
                        "intro_line": intro_line,
                        "reaction_line": reaction_line,
                        "visual": visual,
                        "rank_reason": rank_reason,
                    }
                )
            )

        # Defensively adopt hook, title_candidates, thumbnail_text from Gemini output.
        # Each field is only updated when Gemini returns a valid, non-empty value.
        # hookはGeminiが文字数指定を無視しても超過文を絶対に採用しないよう長さも検証する。
        # 不採用時は draft.hook(title_ja翻訳前の英語ベースのフォールバック)ではなく、
        # 翻訳後の new_segments[0].title_ja から再生成する(タイトルは日本語なのに
        # フックだけ英語のまま、というズレを防ぐ)。
        raw_hook = result.get("hook")
        fallback_hook = template_hook(new_segments[0].title_ja) if new_segments else draft.hook
        new_hook = (
            raw_hook
            if isinstance(raw_hook, str)
            and raw_hook.strip()
            and len(raw_hook.strip()) <= HOOK_MAX_CHARS + 10
            else fallback_hook
        )

        raw_candidates = result.get("title_candidates")
        if (
            isinstance(raw_candidates, list)
            and len(raw_candidates) >= 1
            and all(isinstance(c, str) for c in raw_candidates)
        ):
            new_title_candidates = raw_candidates
            new_title = raw_candidates[0]
        else:
            new_title_candidates = draft.title_candidates
            new_title = draft.title

        raw_thumb_candidates = result.get("thumbnail_text_candidates")
        cleaned_thumb_candidates = []
        if isinstance(raw_thumb_candidates, list):
            cleaned_thumb_candidates = [
                candidate
                for candidate in (
                    _clean_thumbnail_text_candidate(raw_candidate)
                    for raw_candidate in raw_thumb_candidates
                )
                if candidate is not None
            ]
        if (
            len(cleaned_thumb_candidates) >= 1
        ):
            new_thumbnail_candidates = cleaned_thumb_candidates
            new_thumbnail_text = cleaned_thumb_candidates[0]
        else:
            new_thumbnail_candidates = draft.thumbnail_text_candidates
            new_thumbnail_text = draft.thumbnail_text

        narration_script = f"【フック】\n{new_hook}\n\n【オープニング】\n{new_intro}\n\n"
        for seg in new_segments:
            narration_script += f"{seg.narration}\n\n"
        narration_script += f"【まとめ】\n{new_outro}"

        return draft.model_copy(
            update={
                "title": new_title,
                "title_candidates": new_title_candidates,
                "intro": new_intro,
                "outro": new_outro,
                "segments": new_segments,
                "narration_script": narration_script,
                "hook": new_hook,
                "thumbnail_text": new_thumbnail_text,
                "thumbnail_text_candidates": new_thumbnail_candidates,
            }
        )

    except Exception:
        return draft
