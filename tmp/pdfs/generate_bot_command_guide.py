from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, PageBreak, KeepTogether


OUTPUT = "output/pdf/kdrama_bot_command_guide.pdf"


def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#667085"))
    canvas.drawString(17 * mm, 11 * mm, "K-Drama Bot Command Guide")
    canvas.drawRightString(193 * mm, 11 * mm, f"Page {doc.page}")
    canvas.restoreState()


styles = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=24, leading=30, textColor=colors.HexColor("#101828"), spaceAfter=8),
    "subtitle": ParagraphStyle("subtitle", parent=styles["BodyText"], fontSize=11.5, leading=16, textColor=colors.HexColor("#475467"), spaceAfter=12),
    "h1": ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=17, leading=22, textColor=colors.HexColor("#101828"), spaceBefore=12, spaceAfter=6),
    "h2": ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=colors.HexColor("#344054"), spaceBefore=9, spaceAfter=4),
    "body": ParagraphStyle("body", parent=styles["BodyText"], fontSize=10.5, leading=15, textColor=colors.HexColor("#344054"), spaceAfter=6),
    "note": ParagraphStyle("note", parent=styles["BodyText"], fontSize=10, leading=14, textColor=colors.HexColor("#344054"), backColor=colors.HexColor("#F9FAFB"), borderColor=colors.HexColor("#D0D5DD"), borderWidth=0.7, borderPadding=7, spaceBefore=4, spaceAfter=8),
    "code": ParagraphStyle("code", parent=styles["Code"], fontName="Courier", fontSize=10.5, leading=14.5, textColor=colors.HexColor("#101828"), backColor=colors.HexColor("#F2F4F7"), borderColor=colors.HexColor("#D0D5DD"), borderWidth=0.6, borderPadding=8, leftIndent=0, spaceBefore=3, spaceAfter=8),
    "smallcode": ParagraphStyle("smallcode", parent=styles["Code"], fontName="Courier", fontSize=9.5, leading=13, textColor=colors.HexColor("#101828"), backColor=colors.HexColor("#F2F4F7"), borderColor=colors.HexColor("#D0D5DD"), borderWidth=0.6, borderPadding=8, leftIndent=0, spaceBefore=3, spaceAfter=8),
}


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def para(text, style="body"):
    return Paragraph(text, S[style])


def code(text, small=False):
    return Paragraph(esc(text).replace("\n", "<br/>"), S["smallcode" if small else "code"])


def section(title, body=None):
    items = [para(title, "h1")]
    if body:
        items.append(para(body, "body"))
    return items


story = []
story.append(para("K-Drama Bot Command Guide", "title"))
story.append(para("Readable command reference for the current bot code. Admin commands work only for configured admins in private chat.", "subtitle"))
story.append(para("In examples, replace showname with the real show name. Hindi/K-Hindi uses the base command with no category suffix, for example /delete showname 1 1 480p.", "note"))

story += section("1. Category Command Names")
story.append(code("""Hindi / K-Hindi:
base commands:
/delete
/add
/add_poster

other explicit Hindi commands:
/delete_hindi
/add_hindi
/add_poster_hindi
/import_hindi

Other categories:
K-Original       -> _orig
Japanese Drama   -> _jap
CT Drama/C Drama -> _c
Global           -> _glb
Pakistan         -> _pak
Anime            -> _anime"""))

story += section("2. Delete Commands")
story.append(para("Use /delete for Hindi. For other categories, replace /delete_category with /delete_orig, /delete_jap, /delete_c, /delete_glb, /delete_pak, or /delete_anime.", "body"))
story.append(para("Normal numeric season format:", "h2"))
story.append(code("""Hindi:
/delete showname
/delete showname 1
/delete showname 1 1
/delete showname 1 1 480p
/delete showname 1 1 720p
/delete showname 1 1 1080p
/delete showname 1 1 4k

Other category pattern:
/delete_category showname
/delete_category showname 1
/delete_category showname 1 1
/delete_category showname 1 1 480p
/delete_category showname 1 1 720p
/delete_category showname 1 1 1080p
/delete_category showname 1 1 4k"""))
story.append(para("S-season format. Use this when the show name ends with a number, like go go squid 2.", "h2"))
story.append(code("""Hindi:
/delete showname S1
/delete showname S1 1
/delete showname S1 1 480p
/delete showname S1 1 720p
/delete showname S1 1 1080p
/delete showname S1 1 4k

Other category pattern:
/delete_category showname S1
/delete_category showname S1 1
/delete_category showname S1 1 480p
/delete_category showname S1 1 720p
/delete_category showname S1 1 1080p
/delete_category showname S1 1 4k"""))
story.append(para("Exact category examples:", "h2"))
story.append(code("""/delete_orig showname 1 1 480p
/delete_jap showname 1 1 480p
/delete_c showname 1 1 480p
/delete_glb showname 1 1 480p
/delete_pak showname 1 1 480p
/delete_anime showname 1 1 480p

/delete_orig showname S1 1 480p
/delete_jap showname S1 1 480p
/delete_c showname S1 1 480p
/delete_glb showname S1 1 480p
/delete_pak showname S1 1 480p
/delete_anime showname S1 1 480p"""))
story.append(para("Example with show name ending in a number: /delete_c go go squid 2 deletes the full show named go go squid 2. To delete season/episode/quality from that show, use /delete_c go go squid 2 S1 1 720p.", "note"))

story.append(PageBreak())
story += section("3. Add Show / Add Season Commands")
story.append(code("""Hindi:
/add showname
/add showname 1

Specific categories:
/add_hindi showname
/add_orig showname
/add_jap showname
/add_c showname
/add_glb showname
/add_pak showname
/add_anime showname

Add season in specific category:
/add_c showname 1
/add_jap showname 1
/add_orig showname 1

Inline category form:
/add showname > c
/add showname > c 1
/add showname > japanese
/add showname > global 1
/add showname > anime 1"""))
story.append(para("Accepted inline category words include hindi, hindi_dubbed, hindi_dub, regional, orig, cdrama, c, arabic, glb, global, japanese, jap, pakistan, pak, anime.", "note"))

story += section("4. Import Episode Commands")
story.append(para("First send the import command. Then send the video, document, or http/https link.", "body"))
story.append(code("""Import commands:
/import_hindi
/import_orig
/import_jap
/import_c
/import_glb
/import_pak
/import_anime

Basic format:
/import_category showname S1 E1 480p
/import_category showname S1 E1 720p
/import_category showname S1 E1 1080p

Quoted show name:
/import_category "show name" S1 E1 720p

Split parts:
/import_category showname S1 E1 720p P1
/import_category showname S1 E1 720p P2
/import_category showname S1 E1 720p P3

Examples:
/import_c go go squid 2 S1 E1 720p
/import_c "go go squid 2" S1 E1 720p
/import_jap showname S2 E5 1080p P1"""))

story += section("5. Poster Commands")
story.append(para("Send the command first, then send a photo or image file.", "body"))
story.append(code("""Hindi:
/add_poster showname
/add_poster_hindi showname

Other categories:
/add_poster_orig showname
/add_poster_jap showname
/add_poster_c showname
/add_poster_glb showname
/add_poster_pak showname
/add_poster_anime showname"""))

story.append(PageBreak())
story += section("6. User Commands")
story.append(code("""/start
/start support
/start category__show_slug
/help
/search showname
/favorites
/recent_updates
/request drama name
/history
/report
/trending
/top10
/popular
/fav"""))
story.append(para("Deep links like /start category__show_slug are usually generated by bot buttons, favorites, search results, or inline results.", "note"))

story += section("7. Admin Utility Commands")
story.append(code("""/stats
/selftest
/sync_users
/banned_list

Broadcast:
/broadcast message text
or reply to any message with:
/broadcast

Sticker setting:
reply to a sticker with:
/set_sticker

Unban:
/unban user_id
/unban @username
/unban full name

Lookup:
/get slug_or_hash

User search:
/user_search user_id
/user_search @username
/user_search full name

Report search:
/report_search showname
/report_search user_id"""))

story += section("8. Quick Most-Used Examples")
story.append(code("""Delete Hindi episode quality:
/delete showname 1 1 720p
/delete showname S1 1 720p

Delete CT Drama episode quality:
/delete_c showname 1 1 720p
/delete_c showname S1 1 720p

Delete from show ending with number:
/delete_c go go squid 2 S1 1 720p

Add CT Drama show:
/add_c go go squid 2

Import CT Drama episode:
/import_c go go squid 2 S1 E1 720p

Add CT Drama poster:
/add_poster_c go go squid 2"""))

doc = BaseDocTemplate(
    OUTPUT,
    pagesize=A4,
    leftMargin=17 * mm,
    rightMargin=17 * mm,
    topMargin=16 * mm,
    bottomMargin=18 * mm,
)
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=on_page)])
doc.build(story)
print(OUTPUT)
