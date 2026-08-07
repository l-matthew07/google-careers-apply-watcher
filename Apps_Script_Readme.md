# Google Careers Apply Button Tracker

This script monitors Google Careers job postings to see when the "Apply" button becomes active. It uses a time-driven trigger to run in the background. When the script detects the button, it emails users who requested to watch that specific job link.

## How It Works

1. **Form Input:** Users submit their email address and the specific Google Careers link they want to watch via a Google Form. The form is located here: `https://docs.google.com/forms/d/1JzQkcc8fsnolMbQLZoRrooxmFt-onrWVa8gEPlqnbs4/edit`
2. **Time-Driven Trigger:** The script runs automatically every 5 minutes.
3. **Reference Check:** The script checks a known active job posting to verify that Google's webpage structure has not changed. This acts as a reliable baseline.
4. **Link Scanning:** The script collects all unique job links submitted in the form. It scans each webpage's code for the exact HTML ID used for the apply button (`id="apply-action-button"`). 
5. **Notification System:** If the button is found on a requested link, the script emails the user. It tracks notifications using a combination of the user's email and the requested job link. This ensures users receive exactly one notification per job they watch, even if they track multiple roles.

## Setup Instructions

1. Open Google Apps Script (`script.google.com`).
2. Paste the contents of `Code.gs` into the editor.
3. Click the **Triggers** icon (the alarm clock) on the left sidebar.
4. Add a new trigger for the `checkCareersPages` function. Set the event source to **Time-driven**, set it to a **Minutes timer**, and choose **Every 5 minutes**.
5. Save the trigger. The script will now run silently in the background. You can check the **Executions** tab to view the logs.