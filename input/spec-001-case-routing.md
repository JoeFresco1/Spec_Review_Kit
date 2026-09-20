---
stakeholder: true
order: 1
title: Case Routing
---

# Case Routing {#routing}

How incoming cases reach the right employee with the right priority.

## Automatic Assignment {#routing.assignment}

Cases are automatically assigned to eligible employees based on workload, qualifications, and SLA priority.

### Requirements

- Employees can be filtered by qualification before assignment.
- Current workload should influence which employee receives a case.
- Cases with tighter SLA targets should be assigned first.

### Open questions

- Should assignment consider time zone or shift schedule in the first release?

## Manual Override {#routing.override}

Supervisors can reassign any open case at any time.

### Requirements

- Overrides require a reason.
- The previous assignment stays visible in the case history.

## Escalation Rules {#routing.escalation}

Cases that breach or approach their SLA are escalated to the responsible supervisor.

### Requirements

- Warning at 80% of the SLA window.
- Escalation at 100% of the SLA window.
- Escalations notify both the assignee and the supervisor.

### Internal behavior

<!-- internal -->

Implementation detail: escalation jobs run every minute through the scheduler and write to `escalation_events`. This section is stripped from the stakeholder build.
