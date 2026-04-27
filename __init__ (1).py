"""
Anki Oral Exam Agent v3
Claude AI + PubMed + Save to Card + Regenerate + Chat
"""

def _load():
    from aqt import mw, gui_hooks
    from aqt.qt import (QAction, QKeySequence, QDialog, QVBoxLayout, QHBoxLayout,
                        QPushButton, QLabel, QSizePolicy, QThread, pyqtSignal,
                        QShortcut, QTextEdit, Qt)
    from aqt.webview import AnkiWebView
    import os, re, base64, json, urllib.request, urllib.error

    # Load API key from local config file (never stored on GitHub)
    import os as _os
    _config_path = _os.path.join(_os.path.dirname(__file__), 'config.json')
    _api_cfg = {}
    if _os.path.exists(_config_path):
        with open(_config_path, 'r') as _f:
            _api_cfg = json.load(_f)
    API_KEY = _api_cfg.get("api_key", "")
    if not API_KEY:
        from aqt.utils import showWarning
        showWarning("Oral Exam Agent: No API key found.\n\n"
                    "Please create a file called config.json in your add-on folder with:\n"
                    '{"api_key": "your-key-here"}')
    MODEL     = "claude-sonnet-4-5"
    MIME      = {".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",
                 ".gif":"image/gif",".webp":"image/webp"}

    SYSTEM = """You are a medical education expert helping a student prepare for oral exams.
Read the question carefully, analyse all provided content, then choose the most logical structure:
- Mechanism/pathway → sequential flow
- Comparison → parallel side-by-side
- Clinical → Presentation → Diagnosis → Treatment → Complications
- Concept → Define → Explain → Clinical relevance
- Drug → MOA → Indications → Side effects → Contraindications
Use bold **headers**. Keep points concise but complete — suitable for speaking aloud.
Always end with **Key Points to Remember** (2-4 bullets).
Output clean Markdown. Do NOT say "Based on the card…" — answer directly as if speaking to an examiner."""

    # ── Helpers ───────────────────────────────────────────────────────────────
    def strip_html(html):
        t = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
        t = re.sub(r'<p[^>]*>', '\n', t, flags=re.IGNORECASE)
        t = re.sub(r'<[^>]+>', '', t)
        t = t.replace('&nbsp;',' ').replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').replace('&quot;','"')
        return re.sub(r'\n{3,}', '\n\n', t).strip()

    def md2html(md):
        h = md
        h = re.sub(r'^### (.+)$', r'<h3>\1</h3>', h, flags=re.MULTILINE)
        h = re.sub(r'^## (.+)$',  r'<h2>\1</h2>', h, flags=re.MULTILINE)
        h = re.sub(r'^# (.+)$',   r'<h1>\1</h1>', h, flags=re.MULTILINE)
        h = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', h)
        h = re.sub(r'\*(.+?)\*',     r'<em>\1</em>', h)
        lines, out, in_ul = h.split('\n'), [], False
        for line in lines:
            if re.match(r'^[-•] ', line):
                if not in_ul: out.append('<ul>'); in_ul=True
                out.append(f'<li>{line[2:]}</li>')
            elif re.match(r'^\d+\. ', line):
                if not in_ul: out.append('<ul>'); in_ul=True
                out.append(f'<li>{re.sub(r"^\d+\. ","",line,1)}</li>')
            else:
                if in_ul: out.append('</ul>'); in_ul=False
                out.append(f'<p>{line}</p>' if line.strip() and not line.strip().startswith('<') else line)
        if in_ul: out.append('</ul>')
        return '\n'.join(out)

    def make_page(answer_md, chat_html=""):
        return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
body{{background:#0b0e16;color:#dde2f2;font-family:Georgia,serif;font-size:15px;
     line-height:1.75;padding:20px 28px 40px;margin:0}}
h1{{color:#5b9cf6;font-size:20px;border-bottom:1px solid #252a40;padding-bottom:7px;margin:22px 0 10px}}
h2{{color:#b8c2e8;font-size:17px;border-bottom:1px solid #1e2235;padding-bottom:5px;margin:18px 0 8px}}
h3{{color:#8b7cf8;font-family:monospace;font-size:10px;text-transform:uppercase;
    letter-spacing:1px;margin:16px 0 5px}}
p{{margin:5px 0 9px}}
strong{{color:#b8c2e8}}
ul{{list-style:none;padding:0;margin:6px 0}}
li{{padding:2px 0 2px 18px;position:relative}}
li::before{{content:'▸';position:absolute;left:0;color:#5b9cf6;font-size:10px;top:6px}}
.chat-section{{margin-top:28px;border-top:1px solid #252a40;padding-top:16px}}
.chat-bubble-user{{background:#1a2235;border-radius:8px;padding:10px 14px;
                   margin:8px 0;font-size:13px;color:#8bb4e8;font-family:monospace}}
.chat-bubble-ai{{background:#0d1f18;border-left:3px solid #34d399;border-radius:0 8px 8px 0;
                 padding:10px 14px;margin:8px 0;font-size:14px}}
</style></head><body>
{md2html(answer_md)}
{chat_html}
</body></html>"""

    LOADING_HTML = """<!DOCTYPE html><html><head><style>
body{{background:#0b0e16;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.ring{{width:38px;height:38px;border:3px solid #1e2540;border-top-color:#5b9cf6;border-radius:50%;
      animation:spin .75s linear infinite;margin:0 auto 14px}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.lbl{{color:#5b6488;font-family:monospace;font-size:12px;letter-spacing:1.5px;text-align:center}}
.sub{{color:#3a4060;font-family:monospace;font-size:10px;text-align:center;margin-top:5px}}
</style></head><body><div><div class="ring"></div>
<div class="lbl">ANALYZING CARD</div>
<div class="sub">Claude is reading the content…</div>
</div></body></html>"""

    # ── Claude API ─────────────────────────────────────────────────────────────
    def call_claude(messages_list, system=None):
        payload = json.dumps({
            "model": MODEL,
            "max_tokens": 2000,
            "system": system or SYSTEM,
            "messages": messages_list
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=payload,
            headers={"Content-Type":"application/json",
                     "x-api-key": API_KEY,
                     "anthropic-version":"2023-06-01"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())["content"][0]["text"]

    # ── PubMed ─────────────────────────────────────────────────────────────────
    def get_pubmed(question, answer_text):
        try:
            stop = {"what","how","why","when","is","are","the","a","an","of","in",
                    "to","and","or","for","with","that","this","describe","explain"}
            words = re.findall(r'\b[a-zA-Z]{3,}\b', f"{question} {answer_text[:200]}")
            query = " ".join([w for w in words if w.lower() not in stop][:8])
            import urllib.parse
            params = urllib.parse.urlencode({"db":"pubmed","term":query,"retmax":3,"retmode":"json","sort":"relevance"})
            with urllib.request.urlopen(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{params}", timeout=8) as r:
                ids = json.loads(r.read())["esearchresult"].get("idlist",[])
            if not ids: return [], ""
            params2 = urllib.parse.urlencode({"db":"pubmed","id":",".join(ids),"retmode":"json"})
            with urllib.request.urlopen(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?{params2}", timeout=8) as r:
                data = json.loads(r.read()).get("result",{})
            articles = []
            for pmid in ids:
                if pmid in data:
                    a = data[pmid]
                    articles.append({"pmid":pmid,"title":a.get("title",""),
                                     "journal":a.get("fulljournalname",""),"date":a.get("pubdate",""),
                                     "url":f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"})
            context = "\n".join(f"- {a['title']} ({a['journal']}, {a['date']})" for a in articles)
            return articles, context
        except:
            return [], ""

    # ── Worker thread ──────────────────────────────────────────────────────────
    class Worker(QThread):
        done  = pyqtSignal(str)
        error = pyqtSignal(str)
        def __init__(self_, messages, system=None):
            super().__init__()
            self_.messages = messages
            self_.system   = system
        def run(self_):
            try:
                self_.done.emit(call_claude(self_.messages, self_.system))
            except urllib.error.HTTPError as e:
                self_.error.emit(f"API error {e.code}: {e.read().decode()}")
            except Exception as e:
                self_.error.emit(str(e))

    # ── Save answer to Anki card ───────────────────────────────────────────────
    def save_to_card(card, answer_md, field_name="Oral Answer"):
        try:
            note = card.note()
            if field_name not in note:
                from aqt.utils import showWarning
                showWarning(
                    f"Field '{field_name}' not found in this note type.\n\n"
                    f"To add it:\nTools → Manage Note Types → your type → Fields → Add → '{field_name}'\n\n"
                    f"Then sync Anki and it will appear on your iPad."
                )
                return False
            note[field_name] = md2html(answer_md)
            note.flush()
            mw.col.mod_schema(check=False)
            return True
        except Exception as e:
            from aqt.utils import showWarning
            showWarning(f"Could not save to card: {e}")
            return False

    # ── Main dialog ────────────────────────────────────────────────────────────
    def launch_oral_agent():
        reviewer = mw.reviewer
        if not reviewer or not reviewer.card:
            from aqt.utils import showWarning
            showWarning("Please open a card in the reviewer first.")
            return

        card = reviewer.card
        col  = mw.col

        # Extract card content
        q_html = card.question()
        a_html = card.answer()
        parts  = re.split(r'<hr[^>]*>', a_html, maxsplit=1)
        a_html_clean = parts[-1] if len(parts) > 1 else a_html
        question    = strip_html(q_html)
        answer_text = strip_html(a_html_clean)

        images = []
        for fname in re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', a_html_clean, re.IGNORECASE):
            fp = os.path.join(col.media.dir(), os.path.basename(fname))
            if os.path.exists(fp):
                ext = os.path.splitext(fname)[1].lower()
                with open(fp,"rb") as f:
                    images.append({"name":os.path.basename(fname),
                                   "media_type":MIME.get(ext,"image/jpeg"),
                                   "data":base64.standard_b64encode(f.read()).decode()})

        # Build initial message for Claude
        def build_initial_messages(pubmed_context=""):
            content = []
            text = f"**EXAM QUESTION:**\n{question}\n\n**CARD ANSWER CONTENT:**\n{answer_text}\n"
            if pubmed_context:
                text += f"\n**RELEVANT PUBMED ARTICLES:**\n{pubmed_context}\n"
            text += "\nGenerate a structured oral exam answer."
            content.append({"type":"text","text":text})
            for img in images:
                content.append({"type":"image","source":{"type":"base64",
                    "media_type":img["media_type"],"data":img["data"]}})
                content.append({"type":"text","text":f"[Image: {img['name']} — incorporate relevant info]"})
            return [{"role":"user","content":content}]

        # State
        state = {
            "answer_md": "",
            "pubmed_articles": [],
            "pubmed_context": "",
            "chat_history": [],   # list of {"role","content"} for follow-up chat
            "chat_html": "",
            "workers": []
        }

        # ── Build dialog ──────────────────────────────────────────────────────
        dlg = QDialog(mw)
        dlg.setWindowTitle("⚕ Oral Exam Answer")
        dlg.resize(720, 860)
        dlg.setStyleSheet("background:#0b0e16;")

        root = QVBoxLayout(dlg)
        root.setContentsMargins(0,0,0,0)
        root.setSpacing(0)

        # Top status bar
        topbar = QHBoxLayout()
        topbar.setContentsMargins(12,8,12,6)
        status = QLabel("⏳  Generating…")
        status.setStyleSheet("color:#4a5070;font-size:11px;font-family:monospace;")
        topbar.addWidget(status)
        topbar.addStretch()

        def make_btn(text, color="#5b9cf6", bg="#0d1829", border="#1e3560"):
            b = QPushButton(text)
            b.setStyleSheet(f"QPushButton{{background:{bg};color:{color};border:1px solid {border};"
                           f"border-radius:6px;padding:4px 12px;font-size:11px;font-family:monospace;}}"
                           f"QPushButton:hover{{background:#112240;border-color:{color};}}"
                           f"QPushButton:disabled{{color:#2a3a50;border-color:#151e30;}}")
            return b

        regen_btn = make_btn("↺  Regenerate")
        save_btn  = make_btn("💾  Save to Card", color="#34d399", border="#1a4a3a", bg="#0a1f18")
        close_btn = make_btn("✕", color="#4a5070", border="#252a40", bg="#161924")

        regen_btn.setEnabled(False)
        save_btn.setEnabled(False)

        for b in [regen_btn, save_btn, close_btn]:
            topbar.addWidget(b)

        root.addLayout(topbar)

        sep = QLabel(); sep.setFixedHeight(1); sep.setStyleSheet("background:#1e2235;")
        root.addWidget(sep)

        # WebView
        web = AnkiWebView(dlg)
        web.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        web.setHtml(LOADING_HTML)
        root.addWidget(web)

        # Chat input area
        chat_sep = QLabel(); chat_sep.setFixedHeight(1); chat_sep.setStyleSheet("background:#1e2235;")
        root.addWidget(chat_sep)

        chat_row = QHBoxLayout()
        chat_row.setContentsMargins(10,6,10,8)
        chat_input = QTextEdit()
        chat_input.setFixedHeight(52)
        chat_input.setPlaceholderText("Ask a follow-up question about this topic…")
        chat_input.setStyleSheet("QTextEdit{background:#12151f;color:#c8cce8;border:1px solid #2e3250;"
                                 "border-radius:6px;padding:6px 8px;font-size:13px;font-family:Georgia,serif;}"
                                 "QTextEdit:focus{border-color:#5b9cf6;}")
        chat_row.addWidget(chat_input)

        ask_btn = make_btn("Ask  ➤", color="#8b7cf8", border="#2e2060", bg="#0d0a1f")
        ask_btn.setFixedWidth(80)
        ask_btn.setEnabled(False)
        chat_row.addWidget(ask_btn)
        root.addLayout(chat_row)

        QShortcut(QKeySequence("Escape"), dlg, dlg.close)
        close_btn.clicked.connect(dlg.close)

        # ── Generation logic ──────────────────────────────────────────────────
        def start_generation():
            status.setText("⏳  Generating…")
            status.setStyleSheet("color:#4a5070;font-size:11px;font-family:monospace;")
            regen_btn.setEnabled(False)
            save_btn.setEnabled(False)
            ask_btn.setEnabled(False)
            web.setHtml(LOADING_HTML)

            # Fetch PubMed first (fast, sync is fine for small request)
            def do_generate():
                articles, ctx = get_pubmed(question, answer_text)
                state["pubmed_articles"] = articles
                state["pubmed_context"]  = ctx
                msgs = build_initial_messages(ctx)
                state["chat_history"] = list(msgs)  # seed chat with initial context
                w = Worker(msgs)
                w.done.connect(on_answer_done)
                w.error.connect(on_error)
                state["workers"].append(w)
                w.start()

            # Run PubMed + Claude in thread
            class SetupWorker(QThread):
                ready = pyqtSignal()
                def run(self_): self_.ready.emit()
            sw = SetupWorker()
            sw.ready.connect(do_generate)
            state["workers"].append(sw)
            sw.start()

        def on_answer_done(md):
            state["answer_md"] = md
            # Append assistant answer to chat history
            state["chat_history"].append({"role":"assistant","content":md})
            status.setText("✓  Ready" + (f"  •  {len(state['pubmed_articles'])} PubMed" if state['pubmed_articles'] else ""))
            status.setStyleSheet("color:#34d399;font-size:11px;font-family:monospace;")
            regen_btn.setEnabled(True)
            save_btn.setEnabled(True)
            ask_btn.setEnabled(True)
            web.setHtml(make_page(md, state["chat_html"]))

        def on_error(msg):
            status.setText("✗  Error")
            status.setStyleSheet("color:#f87171;font-size:11px;font-family:monospace;")
            from aqt.utils import showWarning
            showWarning(f"Oral Agent Error:\n\n{msg}")

        # Regenerate
        def on_regen():
            state["chat_html"] = ""
            state["chat_history"] = []
            start_generation()

        regen_btn.clicked.connect(on_regen)

        # Save to card
        def on_save():
            if not state["answer_md"]: return
            ok = save_to_card(card, state["answer_md"])
            if ok:
                save_btn.setText("✓  Saved!")
                save_btn.setEnabled(False)
                status.setText("✓  Saved — sync Anki to push to iPad")
                status.setStyleSheet("color:#34d399;font-size:11px;font-family:monospace;")

        save_btn.clicked.connect(on_save)

        # ── Chat logic ────────────────────────────────────────────────────────
        def on_ask():
            question_text = chat_input.toPlainText().strip()
            if not question_text or not state["answer_md"]: return

            chat_input.clear()
            ask_btn.setEnabled(False)
            status.setText("⏳  Thinking…")
            status.setStyleSheet("color:#8b7cf8;font-size:11px;font-family:monospace;")

            # Add user message to history
            state["chat_history"].append({"role":"user","content":question_text})

            # Add user bubble to display
            state["chat_html"] += f'<div class="chat-bubble-user">🎓 {question_text}</div>'
            web.setHtml(make_page(state["answer_md"], state["chat_html"] + '<div class="chat-bubble-ai" style="color:#4a5070;font-style:italic">Thinking…</div>'))

            chat_system = (SYSTEM + "\n\nYou are now in a follow-up Q&A. "
                          "The student may ask clarifying questions about the topic. "
                          "Keep answers focused, clinically relevant, and concise. "
                          "Use Markdown formatting.")

            w = Worker(list(state["chat_history"]), system=chat_system)

            def on_chat_done(reply):
                state["chat_history"].append({"role":"assistant","content":reply})
                state["chat_html"] += f'<div class="chat-bubble-ai">{md2html(reply)}</div>'
                web.setHtml(make_page(state["answer_md"], state["chat_html"]))
                status.setText("✓  Ready")
                status.setStyleSheet("color:#34d399;font-size:11px;font-family:monospace;")
                ask_btn.setEnabled(True)

            w.done.connect(on_chat_done)
            w.error.connect(on_error)
            state["workers"].append(w)
            w.start()

        ask_btn.clicked.connect(on_ask)

        # Ctrl+Enter to send chat
        class ChatInput(QTextEdit):
            def keyPressEvent(self_, e):
                if e.key() == Qt.Key.Key_Return and e.modifiers() == Qt.KeyboardModifier.ControlModifier:
                    on_ask()
                else:
                    super().keyPressEvent(e)

        # Start!
        start_generation()
        dlg.show()


    # ── Auto-update check ─────────────────────────────────────────────────────
    def check_for_update():
        try:
            url = "https://raw.githubusercontent.com/odedsegev1-ai/anki-oral-agent/main/version.json"
            with urllib.request.urlopen(url, timeout=5) as r:
                remote = json.loads(r.read()).get("version","0.0.0")
            current = "3.0.0"
            if tuple(int(x) for x in remote.split(".")) > tuple(int(x) for x in current.split(".")):
                from aqt.utils import askUser
                if askUser(f"⚕ Oral Exam Agent: New version {remote} available!\n\nInstall update now? (Anki will need to restart)"):
                    install_update()
        except:
            pass  # Silent fail — no internet or GitHub down

    def install_update():
        try:
            import shutil, zipfile
            addon_dir = _os.path.dirname(__file__)
            zip_url = "https://github.com/odedsegev1-ai/anki-oral-agent/archive/refs/heads/main.zip"
            zip_path = _os.path.join(addon_dir, "_update.zip")
            tmp_dir  = _os.path.join(addon_dir, "_update_tmp")
            with urllib.request.urlopen(zip_url, timeout=30) as r:
                with open(zip_path, "wb") as f: f.write(r.read())
            os.makedirs(tmp_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as z: z.extractall(tmp_dir)
            src = _os.path.join(tmp_dir, "anki-oral-agent-main", "__init__.py")
            dst = _os.path.join(addon_dir, "__init__.py")
            if _os.path.exists(src):
                shutil.copy2(src, dst)
            for p in [zip_path, tmp_dir]:
                try:
                    if _os.path.isfile(p): _os.remove(p)
                    elif _os.path.isdir(p): shutil.rmtree(p)
                except: pass
            from aqt.utils import showInfo
            showInfo("✓ Oral Exam Agent updated!\nPlease restart Anki.")
        except Exception as e:
            from aqt.utils import showWarning
            showWarning(f"Update failed: {e}")

    # ── Menu ──────────────────────────────────────────────────────────────────
    def setup_menu():
        action = QAction("⚕  Oral Exam Answer", mw)
        action.setShortcut(QKeySequence("Ctrl+Shift+O"))
        action.triggered.connect(launch_oral_agent)
        mw.form.menuTools.addAction(action)

    # ── Floating button via JS ─────────────────────────────────────────────────
    INJECT_JS = """
    (function(){
        if(document.getElementById('oral-agent-btn'))return;
        var b=document.createElement('button');
        b.id='oral-agent-btn';
        b.innerHTML='⚕ Oral Answer';
        b.style.cssText='position:fixed;bottom:90px;right:24px;z-index:99999;'
            +'background:#0d1829;color:#5b9cf6;border:1px solid #1e3560;'
            +'border-radius:9px;padding:9px 20px;font-family:monospace;'
            +'font-size:13px;cursor:pointer;box-shadow:0 4px 20px rgba(0,0,0,0.5);';
        b.onmouseover=function(){this.style.background='#112240';this.style.borderColor='#5b9cf6';};
        b.onmouseout=function(){this.style.background='#0d1829';this.style.borderColor='#1e3560';};
        b.onclick=function(){pycmd('oral_agent');};
        document.body.appendChild(b);
    })();
    """
    REMOVE_JS = "var b=document.getElementById('oral-agent-btn');if(b)b.remove();"

    def on_answer(card):
        if mw.reviewer: mw.reviewer.web.eval(INJECT_JS)

    def on_question(card):
        if mw.reviewer: mw.reviewer.web.eval(REMOVE_JS)

    def on_pycmd(handled, cmd, reviewer):
        if cmd == "oral_agent":
            launch_oral_agent()
            return (True, None)
        return handled

    gui_hooks.reviewer_did_show_answer.append(on_answer)
    gui_hooks.reviewer_did_show_question.append(on_question)
    gui_hooks.webview_did_receive_js_message.append(on_pycmd)
    setup_menu()


from aqt import gui_hooks
gui_hooks.main_window_did_init.append(_load)
