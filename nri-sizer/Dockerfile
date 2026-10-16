ARG GO_TAG="1.26.9-bookworm"
FROM golang:$GO_TAG AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /nri-sizer .

# Root: the plugin writes per-container files under a hostPath.
FROM gcr.io/distroless/static-debian12:latest
COPY --from=build /nri-sizer /nri-sizer
ENTRYPOINT ["/nri-sizer"]
