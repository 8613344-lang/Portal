FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN addgroup --system portal && adduser --system --ingroup portal portal
COPY --chown=portal:portal . .
RUN mkdir -p /app/media /app/staticfiles && chown -R portal:portal /app/media /app/staticfiles
USER portal
EXPOSE 8000
CMD ["sh", "deploy/start.sh"]
