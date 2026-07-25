# Data Source, Attribution, and Terms of Use

## Data source

This project uses static and, in future phases, real-time transit data
provided by the Société de transport de Montréal (STM).

Source:

- Société de transport de Montréal
- STM Developers Portal
- STM GTFS and GTFS-Realtime data

Official developer information:

https://www.stm.info/fr/a-propos/developpeurs

## Licence

STM data is made available under the Creative Commons Attribution 4.0
International licence, also known as CC BY 4.0.

The data may be shared and adapted provided that appropriate attribution
is given to the STM.

Licence information:

https://creativecommons.org/licenses/by/4.0/

## Attribution

Data source:

Société de transport de Montréal (STM)

This project acknowledges the STM as the source of the GTFS and
GTFS-Realtime data used in the analysis.

## Unofficial project disclaimer

This is an independent and unofficial portfolio project.

It is not affiliated with, sponsored by, endorsed by, or associated with
the Société de transport de Montréal.

The analyses, interpretations, quality assessments, reports, and opinions
presented in this project are those of the project author and do not
represent the views of the STM.

The project must not use the STM logo, trademarks, or visual identity in a
way that could suggest an official association or endorsement.

## Data availability and accuracy

STM data and API services are provided as-is and according to availability.

The STM does not guarantee:

- continuous service availability;
- complete or uninterrupted API access;
- data accuracy;
- data security;
- the absence of errors;
- the continued availability of a specific endpoint or feed.

The pipeline must therefore handle unavailable, incomplete, stale, empty,
or invalid responses without assuming that the STM service is always
operational.

## Metro schedule restriction

STM metro schedules are provided for informational purposes and are
primarily intended to help determine trip duration.

This project must not use these schedules to develop a public metro
schedule application.

The current project focuses primarily on bus GTFS and GTFS-Realtime data
quality and operational reliability analysis.

## API credentials

The STM API key is confidential project configuration.

It must never be:

- committed to Git;
- included in configuration JSON files;
- stored in `.env.example`;
- printed in logs;
- included in exception messages;
- included in generated reports;
- included in screenshots;
- exposed through GitHub Actions;
- shared in documentation or issue discussions.

The key must be read only from:

```text
STM_GTFS_REALTIME_API_KEY
```

Swagger-generated curl commands may contain the API key. They must never be
copied into source code, tests, fixtures, documentation, logs, screenshots,
issues, or version control.
