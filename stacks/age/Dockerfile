FROM cgr.dev/chainguard/wolfi-base@sha256:fac38d12efdb4bf43ac9e599a31db10a27ad5dd71e5f1618790962eda8d66180

ARG AGE_VERSION=v1.3.2

RUN apk add --no-cache ca-certificates curl \
  && curl -fsSL -o /tmp/age.tgz \
    "https://github.com/FiloSottile/age/releases/download/${AGE_VERSION}/age-${AGE_VERSION}-linux-amd64.tar.gz" \
  && tar -xzf /tmp/age.tgz -C /tmp \
  && mv /tmp/age/age /tmp/age/age-keygen /usr/bin/ \
  && rm -rf /tmp/age /tmp/age.tgz

ENTRYPOINT ["age"]
