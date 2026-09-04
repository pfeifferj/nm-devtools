# Release container

```sh
NM_RELEASE_TOKEN=<gitlab-api-token> release/release-container.sh rc1
```

Dry run by default (builds RPMs + tarball in the container, no push); add
`--no-test` for the real thing. `NM_SRC` overrides the NetworkManager checkout
(default `~/rh-src/NetworkManager`). Signing uses the host gpg-agent via a
mounted socket; no keys enter the container. See the script header for all
env overrides.
