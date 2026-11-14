# Not built into anything: `make lint` runs this image over the workflows. The FROM line is
# here so that Dependabot, which reads Dockerfiles, keeps the tag and the digest current
FROM rhysd/actionlint:1.7.12@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667
