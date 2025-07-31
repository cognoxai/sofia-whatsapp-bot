# Estágio 1: Builder - Instala dependências em um ambiente temporário
FROM python:3.11-slim as builder
WORKDIR /install
COPY requirements.txt .
RUN pip install --prefix="/install" -r requirements.txt

# Estágio 2: Runner - Cria a imagem final e segura
FROM python:3.11-slim
RUN useradd --create-home appuser
USER appuser
WORKDIR /home/appuser/app

# Copia as dependências já instaladas do estágio anterior
COPY --from=builder /install /usr/local

# Copia o código da aplicação
COPY main.py .

# Expõe a porta que o Uvicorn irá usar
EXPOSE 8000

# Comando para iniciar o servidor em produção
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
