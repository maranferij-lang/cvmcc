"""Відгук: що сподобалось, що зламалось, чого бракує."""

import streamlit as st

from ui.account import send_feedback
from ui.common import card

st.title("Відгук")
st.markdown(
    '<p class="cvx-page-lead">CVmax зараз у пілоті, і саме твій відгук вирішує, що ми виправимо першим. '
    "Найцінніше: де CVmax помилився, порадив дурницю або щось вигадав.</p>",
    unsafe_allow_html=True,
)
with card("feedback-form"):
    with st.form("feedback", clear_on_submit=True, border=False):
        topic = st.segmented_control(
            "Про що відгук", ["Аналіз CV", "Опитування", "Готове CV", "Куди податись", "Конструктор", "Інше"],
            default="Аналіз CV",
        )
        rating = st.feedback("thumbs")
        message = st.text_area(
            "Що сталось або що покращити", max_chars=2000, height=160,
            placeholder="Напр.: порадив прибрати пункт, який для цієї вакансії важливий; або: не прочитав мій PDF.",
        )
        sent = st.form_submit_button("Надіслати", type="primary")
    if sent:
        if not message.strip() and rating is None:
            st.warning("Постав оцінку або напиши кілька слів.")
        elif send_feedback(f"feedback:{topic or 'Інше'}", rating, message):
            st.success("Дякуємо! Ми читаємо кожен відгук.")
st.caption(
    "Відгук анонімний: ми не зберігаємо твій email разом із ним. Не пиши в ньому телефон чи інші особисті дані."
)
