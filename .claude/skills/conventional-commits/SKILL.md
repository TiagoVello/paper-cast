---
name: conventional-commits
description: Conventional Commits v1.0.0 format. Use when writing or amending a git commit message, or naming a squash merge or PR title.
---

# Conventional Commits

Structure, in order: a subject line of `<type>[(scope)][!]: <description>`, a blank line, an optional body, a blank line, optional footers.

## Type

A noun stating what the change does:

- `feat` — adds a feature to the application or library.
- `fix` — fixes a bug.
- Any other noun for everything else; the conventional set is `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`.

Read `git log` first and reuse the type vocabulary the repo already uses.

## Scope

Optional, in parentheses after the type: a noun naming the section of the codebase touched. Take scope names from `git log` too, so they stay a closed set.

## Description

Follows the colon and a single space: a brief summary of what the change does, on one line.

## Body

Begins one blank line after the subject. Free-form paragraphs carrying what the diff cannot show — motivation, consequences, rejected alternatives. Include one when the reason for the change is non-obvious.

## Footers

Begin one blank line after the body. Each is a token, then `: ` or ` #`, then a value that may span lines. Tokens use hyphens in place of spaces.

## Breaking changes

A change that breaks consumers is marked in the subject with `!` before the colon, in a `BREAKING CHANGE:` footer describing the break, or both. `BREAKING CHANGE` is the one token that must be uppercase; `BREAKING-CHANGE` is the same token.

## Before committing

Confirm all three: the type matches what the diff does, the scope exists in the repo's history, and every consumer-facing break is marked.
