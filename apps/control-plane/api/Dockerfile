FROM python:3.12-slim
WORKDIR /opt/launch-operations
COPY apps/control-plane/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services services
COPY projects projects
COPY apps/control-plane/api api
WORKDIR /opt/launch-operations/api
USER 65532:65532
ENV PYTHONPATH=/opt/launch-operations
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
