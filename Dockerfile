FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY opticorr ./opticorr
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin opticorr
COPY --from=build /install /usr/local
USER opticorr
WORKDIR /home/opticorr
ENV OPTICORR_DB=/home/opticorr/opticorr.db
# SNMP trap listener (UDP) and web UI/API.
EXPOSE 162/udp 8080
CMD ["python", "-m", "opticorr.main"]
