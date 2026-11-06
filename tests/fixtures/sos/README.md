These files preserve iproute2 7.3.0 output saved during the original diagnostics
review. They cover isolated fixture interfaces and documentation address ranges.
Trailing padding is removed. The VF excerpt follows iproute2's PF/VF counter layout. No network commands are
needed to consume these files.

`ip-routes-all.txt` deliberately includes a gatewayless `unreachable default`
whose family is not recoverable from an unflagged text capture. The importer must
keep that capture unavailable rather than guess its family.
