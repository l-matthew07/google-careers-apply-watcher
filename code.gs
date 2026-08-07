/**
 * Main function to set on a time-driven trigger.
 */
function checkCareersPage() {
  var targetUrl = "https://www.google.com/about/careers/applications/jobs/results/78703249065943750-software-engineer-early-career-campus"; 
  var referenceUrl = "https://www.google.com/about/careers/applications/jobs/results/82087125486314182-manufacturing-structural-test-development-engineer";
  var formId = "1JzQkcc8fsnolMbQLZoRrooxmFt-onrWVa8gEPlqnbs4"; 

  // Fetch the target Early Career job page
  var targetResponse = UrlFetchApp.fetch(targetUrl);
  var targetHtml = targetResponse.getContentText().toLowerCase();
  var targetButtonFound = targetHtml.includes('id="apply-action-button"');

  // Fetch the active reference job page
  var referenceResponse = UrlFetchApp.fetch(referenceUrl);
  var referenceHtml = referenceResponse.getContentText().toLowerCase();
  var referenceButtonFound = referenceHtml.includes('id="apply-action-button"');

  // Silently log the status. No emails are sent here.
  if (referenceButtonFound && !targetButtonFound) {
    console.log("Button was found on the reference job but not on the Early Career job.");
  } else if (!referenceButtonFound && !targetButtonFound) {
    console.log("Button was not found on either job. Google might have changed their webpage structure.");
  } else if (targetButtonFound) {
    console.log("Button found on the Early Career job! Checking for new form submissions.");
    sendEmailsFromForm(formId);
  }
}

/**
 * Helper function to read the form and send emails.
 */
function sendEmailsFromForm(formId) {
  var form = FormApp.openById(formId);
  var responses = form.getResponses();
  
  var scriptProperties = PropertiesService.getScriptProperties();
  
  // Load the list of already emailed addresses. If it doesn't exist, create an empty object.
  var sentEmailsString = scriptProperties.getProperty('sentEmails');
  var sentEmails = sentEmailsString ? JSON.parse(sentEmailsString) : {};
  
  var subject = "Google Early Career Application is Open";
  var message = "Please apply to the Google Early Career position. The apply button has been updated on the website: https://www.google.com/about/careers/applications/jobs/results/78703249065943750-software-engineer-early-career-campus";

  var emailsSentCount = 0;

  for (var i = 0; i < responses.length; i++) {
    var itemResponses = responses[i].getItemResponses();
    
    // Grab the answer to the first question ("Your Email")
    if (itemResponses.length > 0) {
      // Get the email and make it lowercase so "Test@gmail.com" matches "test@gmail.com"
      var emailAddress = itemResponses[0].getResponse().trim().toLowerCase();
      
      // Check if the cell has an email and if it is NOT in our sent list
      if (emailAddress && emailAddress !== "" && !sentEmails[emailAddress]) {
        MailApp.sendEmail(emailAddress, subject, message);
        
        // Add this email to our tracking list
        sentEmails[emailAddress] = true;
        emailsSentCount++;
      }
    }
  }
  
  // Save the updated list of emails back to the properties
  if (emailsSentCount > 0) {
    scriptProperties.setProperty('sentEmails', JSON.stringify(sentEmails));
    console.log("Successfully sent " + emailsSentCount + " new emails.");
  } else {
    console.log("No new form submissions to email.");
  }
}

/**
 * Run this function manually to test if the script can read your form correctly.
 * It will NOT send any emails.
 */
function testReadForm() {
  var formId = "1JzQkcc8fsnolMbQLZoRrooxmFt-onrWVa8gEPlqnbs4"; 
  var form = FormApp.openById(formId);
  var responses = form.getResponses();
  
  console.log("Test: Found " + responses.length + " total responses in the form.");
  
  for (var i = 0; i < responses.length; i++) {
    var itemResponses = responses[i].getItemResponses();
    
    if (itemResponses.length > 0) {
      var emailAddress = itemResponses[0].getResponse();
      console.log("Row " + (i + 1) + " Email found: " + emailAddress);
    } else {
      console.log("Row " + (i + 1) + " has no email answer.");
    }
  }
}

/**
 * Run this to reset the script if you need to test it again.
 */
function resetScript() {
  var scriptProperties = PropertiesService.getScriptProperties();
  scriptProperties.deleteProperty('sentEmails');
  console.log("Script reset. All users in the form will receive an email again next time the button is found.");
}