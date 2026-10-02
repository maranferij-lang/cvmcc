"""Головна: що таке CVmax і з чого почати."""

import base64

import streamlit as st

from cvmax import config
from cvmax.cv_render import pdf_preview, render_pdf
from cvmax.demo import demo_built_cv
from ui.account import auth_configured

st.html('<div class="cvx-wide"></div>')  # ширша сторінка, див. assets/style.css
ACCOUNTS = auth_configured()  # без входу результати живуть лише у вкладці


@st.cache_data
def cv_thumbnail() -> str:
    """Перша сторінка демо-CV з нашого шаблону, щоб показати, який PDF отримає юзер."""
    png = pdf_preview(render_pdf(demo_built_cv()), scale=1.2)[0]
    return base64.b64encode(png).decode()


# ---------- Герой ----------
left, right = st.columns([1.05, 1], gap="large", vertical_alignment="center")
with left:
    st.markdown(
        """
<div class="cvx-hero">
  <span class="cvx-eyebrow"><i></i>Безплатний пілот для студентів КШЕ</span>
  <h1>CV, з яким <em class="hl">кличуть</em> на співбесіду</h1>
  <p class="lead">Завантаж CV і вкажи вакансію. CVmax покаже, що рекрутер пропустить, а що зачепить,
  перепише слабкі пункти і розпитає про досвід, який ти недооцінюєш. Нічого не вигадуючи.</p>
</div>
""",
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    if c1.button("Проаналізувати CV", type="primary", icon=":material/arrow_forward:", width="stretch"):
        st.switch_page("views/analyze.py")
    if c2.button("Немає CV? Зібрати", width="stretch"):
        st.switch_page("views/builder.py")
    st.caption(("Вхід через Google" if ACCOUNTS else "Без реєстрації") + " · PDF або DOCX англійською · файл CV не зберігається")
    st.caption("Free AI CV checker for students: tailor your English CV to a real vacancy, without invented facts.")

with right:
    st.markdown(
        """
<div class="cvx-device">
  <span class="cvx-float">+26 балів після правок</span>
  <div class="cvx-row">
    <div>
      <div class="cvx-muted">АНАЛІЗ ПІД РОЛЬ</div>
      <div class="cvx-title">Business Analyst · фінтех</div>
    </div>
    <div class="cvx-score" style="--p:84"><b>84</b></div>
  </div>
  <div class="cvx-diff">
    <div class="was"><span class="tag">Було</span>Responsible for making reports in Excel</div>
    <div class="now"><span class="tag">Стало</span>Built <span class="cvx-ph">[N]</span> weekly Excel sales reports
    for the regional team, cutting preparation time by <span class="cvx-ph">[X]%</span></div>
  </div>
  <div class="cvx-bubbles">
    <div class="cvx-bubble">Скільки людей приходило на події, які ти організовував(-ла)?</div>
    <div class="cvx-bubble me">Зазвичай 250–300, а на фінал кейс-чемпіонату 400</div>
  </div>
  <div class="cvx-chips">
    <span class="cvx-chip">BA у фінтеху<em>82%</em></span>
    <span class="cvx-chip">Product Analyst<em>74%</em></span>
    <span class="cvx-chip">Consulting Intern<em>61%</em></span>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

# ---------- Можливості ----------
st.markdown(
    f"""
<div class="cvx-section">
  <div class="kicker">Що всередині</div>
  <h2>Не загальні поради, а правки під твою ціль</h2>
  <p>Той самий пункт сильний для консалтингу і зайвий для розробки. CVmax оцінює кожен рядок CV саме під роль,
  яку ти вказав(-ла), і пояснює чому.</p>
</div>
<div class="cvx-bento">
  <div class="cvx-tile w4">
    <h4>Перевірка кожного рядка</h4>
    <p>Лишити, скоротити, переписати чи прибрати. Пункти без дії й результату, дублі та «вихваляння»,
    яке не працює на ціль, CVmax позначає окремо. Якщо CV не влазить на сторінку, підкаже, що різати першим.</p>
    <div class="cvx-diff">
      <div class="was"><span class="tag">Прибрати</span>Interests: travelling, music, gym</div>
      <div class="now"><span class="tag">Лишити</span>Chess: 2nd place, Kyiv U-18 championship</div>
    </div>
  </div>
  <div class="cvx-tile">
    <div class="num">{config.GRILL_MAX_QUESTIONS}</div>
    <h4>питань Grill me</h4>
    <p>Розпитує про числа, масштаб і досвід, якого немає в CV. З відповідей виходять нові пункти.</p>
    <div class="cvx-bubbles">
      <div class="cvx-bubble">Ти робив(-ла) щось з AI для себе?</div>
      <div class="cvx-bubble me">Скрипт, що щодня збирає ціни конкурентів у таблицю</div>
    </div>
  </div>
  <div class="cvx-tile">
    <h4>Куди податись</h4>
    <p>Напрями, де з твоїм CV найбільше шансів, і що шукати на LinkedIn чи Djinni.</p>
    <div class="cvx-fit">
      <div><div class="cvx-row"><span>Data Analyst</span><span>78%</span></div><div class="cvx-bar"><i style="width:78%"></i></div></div>
      <div><div class="cvx-row"><span>BA, консалтинг</span><span>64%</span></div><div class="cvx-bar"><i style="width:64%"></i></div></div>
    </div>
  </div>
  <div class="cvx-tile">
    <h4>Що вивчити</h4>
    <p>Прогалини, які найсильніше піднімуть шанси, з оцінкою часу.</p>
    <ul class="cvx-list">
      <li>SQL: joins, window functions <span>3–4 тижні</span></li>
      <li>IELTS Academic 7.0+ <span>1–2 місяці</span></li>
    </ul>
  </div>
  <div class="cvx-tile">
    <h4>Готове CV у PDF</h4>
    <p>Прийняті правки одразу в чистому шаблоні, який читають і рекрутери, і ATS. Або DOCX, щоб доредагувати.</p>
    <div class="cvx-paper"><img alt="Приклад CV у шаблоні CVmax" src="data:image/png;base64,{cv_thumbnail()}"></div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------- Як це працює ----------
st.markdown(
    """
<div class="cvx-section">
  <div class="kicker">Як це працює</div>
  <h2>Три кроки, близько десяти хвилин</h2>
</div>
<div class="cvx-steps">
  <div class="cvx-step"><b class="n">1</b><div><div class="t">Розкажи про ціль</div>
    <div class="d">Роль, тип компанії і, найкраще, повний текст вакансії. Консалтинг і фінтех чекають різного.</div></div></div>
  <div class="cvx-step"><b class="n">2</b><div><div class="t">Завантаж CV</div>
    <div class="d">PDF або DOCX англійською. Файл живе тільки у вкладці браузера і не зберігається.</div></div></div>
  <div class="cvx-step"><b class="n">3</b><div><div class="t">Прийми правки й завантаж PDF</div>
    <div class="d">Кожну правку приймаєш сам(-а). Grill me додасть те, про що ти забув(-ла) написати.</div></div></div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------- Принципи ----------
st.markdown(
    f"""
<div class="cvx-section">
  <div class="kicker">Принципи</div>
  <h2>Чесно, під програму, без зайвих даних</h2>
</div>
<div class="cvx-principles">
  <div><b>Нічого не вигадує</b><span>Де бракує числа, ставить [X] і питає. Факти, яких немає в CV чи твоїх
  відповідях, підсвічує як підозрілі.</span></div>
  <div><b>Під твою програму</b><span>Окремі критерії для економістів, інженерів, AI, психологів і юристів,
  зібрані з 47 джерел про те, як рекрутери читають CV.</span></div>
  <div><b>Мінімум даних</b><span>Файл CV не зберігається. {"Результати лежать лише у твоєму кабінеті, і їх можна видалити однією кнопкою." if ACCOUNTS else "Реєстрація не потрібна, а результати живуть лише у вкладці браузера."}</span></div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------- Питання ----------
st.markdown('<div class="cvx-section"><div class="kicker">Питання</div><h2>Часті питання</h2></div>',
            unsafe_allow_html=True)
faq = [
    ("Це безплатно?", "Так. Зараз це пілот для студентів КШЕ, користування безплатне. Щоб так і лишалось, "
     "є денні ліміти, вони оновлюються щодня."),
    ("Ви зберігаєте моє CV?", "Файл CV ні: він надсилається моделі тільки для аналізу і зникає, коли закриваєш "
     "вкладку. "
     + ("Результати аналізу зберігаються у твоєму кабінеті, щоб ти міг(-ла) до них повернутись, "
        "і їх можна видалити будь-коли. " if ACCOUNTS else
        "Реєстрації немає, тож результати теж лишаються тільки у вкладці: завантаж PDF, перш ніж її закрити. ")
     + "Деталі на сторінці «Конфіденційність»."),
    ("Чим це краще за ChatGPT?", "CVmax знає критерії під твою програму і ціль, перевіряє кожен рядок CV, питає "
     "замість того, щоб вигадувати, і дає правки, які можна прийняти по одній. А в кінці збирає готовий PDF."),
    ("CV має бути англійською?", "Так, CVmax орієнтований на міжнародні компанії. Поради можна отримувати "
     "українською або англійською."),
    ("Це гарантує роботу?", "Ні. CVmax допомагає зробити CV сильнішим і обрати реалістичні напрями, "
     "але рішення ухвалює роботодавець. Модель може помилятись, тож перевіряй кожну правку."),
    ("Це офіційний сервіс КШЕ?", "Ні. CVmax це незалежний студентський проєкт. Він зроблений для студентів КШЕ, "
     "але школа його не розробляє і не відповідає за нього."),
]
for q, a in faq:
    with st.expander(q):
        st.write(a)

# ---------- Заклик ----------
with st.container(key="cta", horizontal_alignment="center"):
    st.markdown(
        '<div class="cvx-cta-text"><h2>Перевір своє CV під вакансію мрії</h2>'
        "<p>Перший аналіз займає близько хвилини.</p></div>",
        unsafe_allow_html=True,
    )
    with st.container(horizontal=True, horizontal_alignment="center"):
        if st.button("Почати аналіз", type="primary", icon=":material/arrow_forward:", key="cta_bottom"):
            st.switch_page("views/analyze.py")
        if st.button("Куди мені податись?", key="cta_career"):
            st.switch_page("views/career.py")

st.divider()
with st.container(horizontal=True, gap="medium"):
    st.page_link("views/about.py", label="Про нас")
    st.page_link("views/privacy.py", label="Конфіденційність")
    st.page_link("views/terms.py", label="Умови")
    st.page_link("views/feedback.py", label="Відгук")
st.markdown(
    '<div class="cvx-footer">© 2026 CVmax · незалежний студентський проєкт, не є офіційним сервісом '
    "Київської школи економіки.</div>",
    unsafe_allow_html=True,
)
