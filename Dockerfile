FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# PDF руководств нужны только для кнопки «Открыть PDF»; индекс уже в репозитории. Ошибка загрузки сборку не ломает.
ARG FETCH_MANUALS=1
RUN if [ "$FETCH_MANUALS" = "1" ]; then python -m scripts.fetch_manuals || echo "PDF не загружены"; fi
ENV HOST=0.0.0.0 PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "scripts.run_all"]
