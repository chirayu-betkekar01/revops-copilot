# Step 3: Sync HubSpot into raw

## Predictions (before building)

1. HubSpot returns 100 deals per request. How do you get all 2,000?
   Use pagination or cursor.
2. Split the JSON into columns, or store it as-is?
   Split it into columns, because Postgres is a relational database.
3. How do you avoid 4,000 deals after running the sync twice?
   Idempotency key.
4. The sync dies after 1,200 deals. What happens on the next run?
   Continue from where it left off.
5. What do you record about each sync run?
   Status, response, number of records changed, etc.

## After the step: what I got right, what I got wrong

-
