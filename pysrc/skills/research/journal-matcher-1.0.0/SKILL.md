---
name: journal_matcher
version: 1.1.0
description: Match papers to target journals/conferences with impact factor, quartile, review cycle, acceptance rate, and upcoming submission deadlines.
category: research
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: "match | deadlines"
      enum: ["match", "deadlines"]
    title:
      type: string
      description: Paper title.
    abstract:
      type: string
      description: Paper abstract.
    keywords:
      type: array
      description: Paper keywords.
      items:
        type: string
    field:
      type: string
      description: Research field.
    top_k:
      type: integer
      description: Number of top results (default 5).
    include_conferences:
      type: boolean
      description: Include conferences (default true).
    online:
      type: boolean
      description: Query OpenAlex for real-time data (default true).
    lang:
      type: string
      description: "Output language: zh | en"
      enum: ["zh", "en"]
  required:
    - action
keywords: [journal, conference, match, recommend, impact factor, quartile, submission, research, deadline, calendar]
---

# Journal Matcher v1.1.0

## Actions

### `match`
Original behavior — ranked journal/conference recommendations with impact factors, quartiles, review cycles, and acceptance rates.

### `deadlines` ★ NEW
Show upcoming submission deadlines for matched or specified journals:
- Estimates next submission window based on typical cycles (monthly, quarterly, rolling, annual)
- Shows: journal name, next estimated deadline, review cycle, acceptance rate, special issues if known
- For conferences: shows the next occurrence based on typical annual/biennial schedule
- Output: deadline calendar sorted by urgency

