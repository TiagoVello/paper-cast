# paper-cast

Turns a paper into a private YouTube episode: a NotebookLM audio overview, muxed
over the paper's first page. Used from a terminal, and from an Omarchy bar widget
that launches the same work in the background.

## Language

**Source**:
A paper to be discussed — a local PDF, or an arXiv entry named by id or URL.
_Avoid_: input, file, document

**Job**:
A queued unit of work: one or more Sources plus the Steering and settings to
render them, producing exactly one Episode.
_Avoid_: task, request, item

**Episode**:
The audio overview and its cover, as published to the channel. One Job, one
Episode — three Sources in one Job means one Episode discussing all three.
_Avoid_: video, podcast, overview

**Steering**:
The free-text instruction telling the hosts what to concentrate on and who they
are talking to. The reason the product exists: it is what stops the hosts
explaining AdamW to someone who already knows.
_Avoid_: prompt, focus topic, instructions

**Steering preset**:
A named, pre-written Steering — "ML researcher", "Critical read" — flipped
through rather than retyped. Editing the loaded text rewrites that preset in
place; the panel adds and deletes them.

**Queue**:
The ordered set of Jobs waiting to run. Drained one at a time — NotebookLM is a
remote session, not a pool.

**Run directory**:
Where one Job's artifacts land on disk, named after the Episode. Overwritten when
the same Source is run again, because a re-run means "that one came out wrong".

**Setup**:
The idempotent wizard that brings a machine to the point where a Job can run:
prerequisites, the NotebookLM session, the YouTube credential, and the default
Steering. Re-runnable; it only does the parts that are missing.
_Avoid_: install, onboarding, bootstrap
