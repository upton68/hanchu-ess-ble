<!-- Thanks for contributing! Please fill in the sections below. -->

## Summary

<!-- What does this PR change and why? -->

Fixes #<!-- issue number, if applicable -->

## Type of change

- [ ] Bug fix
- [ ] New sensor / read-only register
- [ ] New control (writes to the inverter or a battery)
- [ ] Documentation
- [ ] Refactor / maintenance

## Testing

- [ ] `pytest` passes locally (see CONTRIBUTING.md)
- [ ] Added/updated tests for the change
- [ ] Tested on real hardware (inverter/battery model, Bluetooth source):

<!--
Writes need both: a test that covers the staged value/validation, AND a
confirmed write on real hardware with read-back. Say which model you tested
on, what values you wrote, and what the vendor app showed afterwards.
-->

## Checklist

- [ ] Added a note under `## [Unreleased]` in `CHANGELOG.md`
- [ ] Updated the README / docs if user-facing behaviour changed
- [ ] Anything identifying (addresses, serials) is redacted in `diagnostics.py`
