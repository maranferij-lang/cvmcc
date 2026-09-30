"""Головна: що таке CVMAX і з чого почати."""

import streamlit as st

from ui.common import inject_css

inject_css()

st.markdown(
    """
<div class="cvx-hero">
  <span class="cvx-badge">Безплатний пілот для студентів КШЕ</span>
  <h1>CV, з яким кличуть на співбесіду</h1>
  <p>CVMAX покаже, що покращити в CV під конкретну вакансію, розпитає про досвід, який ти недооцінюєш,
  і підкаже, куди з твоїм CV найреальніше податись.</p>
</div>
""",
    unsafe_allow_html=True,
)
c1, c2, _ = st.columns([1.1, 1.1, 1.3])
if c1.button("Проаналізувати CV", type="primary", icon=":material/description:", use_container_width=True):
    st.switch_page("views/analyze.py")
if c2.button("Куди мені податись?", icon=":material/explore:", use_container_width=True):
    st.switch_page("views/career.py")

st.divider()
st.subheader("Що вміє CVMAX")
features = [
    (":material/fact_check:", "Аналіз під вакансію",
     "Оцінка CV за 7 критеріями саме під роль і компанію, яку ти обрав(-ла), а не в загальному."),
    (":material/compare_arrows:", "Правки «було / стало»",
     "Конкретні нові формулювання. Кожну правку приймаєш або відхиляєш сам(-а)."),
    (":material/forum:", "Grill me",
     "До 8 коротких питань про твій досвід. З відповідей виходять сильні пункти з реальними числами."),
    (":material/explore:", "Куди податись",
     "Кілька напрямів, де з твоїм CV найбільше шансів, і що шукати на LinkedIn чи Djinni."),
    (":material/school:", "Що вивчити",
     "Навички, сертифікати й проєкти, які найсильніше піднімуть шанси, з оцінкою часу."),
    (":material/download:", "Готове CV",
     "Прийняті правки одразу в тексті CV, який можна завантажити в DOCX."),
]
for row in range(0, len(features), 3):
    cols = st.columns(3)
    for col, (icon, title, text) in zip(cols, features[row : row + 3]):
        with col.container(border=True, height="stretch"):
            st.markdown(f"#### {icon} {title}")
            st.write(text)

st.divider()
st.subheader("Як це працює")
steps = [
    ("Розкажи про ціль", "Роль, тип компанії і, найкраще, текст реальної вакансії."),
    ("Завантаж CV", "PDF або DOCX англійською. Нічого не зберігається."),
    ("Отримай правки", "Прийми потрібні, пройди Grill me і завантаж готове CV."),
]
cols = st.columns(3)
for i, (col, (title, text)) in enumerate(zip(cols, steps), 1):
    col.markdown(f'<div class="cvx-step">{i}</div>', unsafe_allow_html=True)
    col.markdown(f"**{title}**")
    col.write(text)

st.divider()
st.subheader("Чому CVMAX")
c1, c2, c3 = st.columns(3)
c1.markdown("**Нічого не вигадує.** Де бракує числа, ставить `[X]` і питає. Підозрілі факти підсвічує.")
c2.markdown("**Під твою програму.** Окремі критерії для економістів, інженерів, AI, психологів і юристів.")
c3.markdown("**Дані не зберігаються.** CV живе тільки у вкладці браузера і зникає після закриття.")

st.divider()
st.subheader("Питання")
faq = [
    ("Це безплатно?", "Так. Зараз це пілот для студентів КШЕ, користування безплатне."),
    ("Ви зберігаєте моє CV?", "Ні. CV надсилається моделі тільки для аналізу і зникає, коли закриваєш вкладку. "
     "Деталі на сторінці «Конфіденційність»."),
    ("CV має бути англійською?", "Так, CVMAX орієнтований на міжнародні компанії. Поради можна отримувати "
     "українською або англійською."),
    ("Це гарантує роботу?", "Ні. CVMAX допомагає зробити CV сильнішим і обрати реалістичні напрями, "
     "але рішення ухвалює роботодавець. Модель може помилятись, тож перевіряй кожну правку."),
    ("Чим це краще за ChatGPT?", "CVMAX знає критерії під твою програму і ціль, питає замість того, щоб вигадувати, "
     "і дає правки, які можна прийняти по одній."),
]
for q, a in faq:
    with st.expander(q):
        st.write(a)

st.divider()
st.subheader("Готовий спробувати?")
if st.button("Почати з аналізу CV", type="primary", icon=":material/arrow_forward:", key="cta_bottom"):
    st.switch_page("views/analyze.py")

st.divider()
f1, f2, f3, _ = st.columns([1, 1, 1, 2])
f1.page_link("views/about.py", label="Про нас")
f2.page_link("views/privacy.py", label="Конфіденційність")
f3.page_link("views/terms.py", label="Умови")
st.markdown('<div class="cvx-footer">© 2026 CVMAX. Зроблено студентом КШЕ для студентів.</div>', unsafe_allow_html=True)
