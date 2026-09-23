# Context

I'm building a web application that allows users to track the traffic viloations (red or orange traffic light) per day and location.
We are a group of parents that are helping to make the daily commute of our community's kids to school safer. Every day in the moring we are aiding them accross streets and crossings.
This is volunteer work, but we want to make the streets safer and raise awareness to local politicians and regulatories.
This app should allow the easy collection of those stats.

The typical workflow:
1. in the morning approx halve an hour before school starts our work starts at a designated locataion (~ 6 locations)
2. during our work we count the traffic violations
3. after our shift we enter that information in the app (location, car crossed on yellow, car crossed traffic light on red, other violations)


# Architecture
The app should be mobile friendly, easy entering of data is key: selecting location, data entry submit
The data-store requirements are minimial. 5 days a week, ~6 locations, so no big database is required
the app should have key protection to avoid mis-use. I trust the users of the app so no personal authentication is required. If easily done a one-time key can be entered and the mobile device of a user can be remembered.

The app needs a statistics function, where users can get summaries of the collected data. examples:
- violations per day/week/month overall
- violations per day/week/month/location
Best as easy readable charts.

## Backend
The application can run in a docker container/k8s. Easily deployable in my homelab

## Design Principle
- Mobile first
- easy to input, no typing where possible