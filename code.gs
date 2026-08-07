/**
 * Main function to set on a time-driven trigger.
 */
function checkCareersPage() {
  var formId = "1JzQkcc8fsnolMbQLZoRrooxmFt-onrWVa8gEPlqnbs4"; 
  var referenceUrl = "https://www.google.com/about/careers/applications/jobs/results/82087125486314182-manufacturing-structural-test-development-engineer";
  
  // 1. Check the active reference job page to ensure Google's site structure hasn't changed
  var referenceResponse = UrlFetchApp.fetch(referenceUrl);
  var referenceHtml = referenceResponse.getContentText().toLowerCase();
  var referenceButtonFound = referenceHtml.includes('id="apply-action-button"');

  if (!referenceButtonFound) {
    console.log("Button was not found on the reference job. Google might have changed their webpage structure.");
  }

  // 2. Read the form responses
  var form = FormApp.openById(formId);
  var responses = form.getResponses();
  
  var usersToNotify = [];
  var uniqueLinksToCheck = {};

  // 3. Extract emails and links from every form submission
  for (var i = 0; i < responses.length; i++) {
    var itemResponses = responses[i].getItemResponses();
    var email = "";
    var targetLink = "";
    
    // Look through the answers in this specific submission
    for (var j = 0; j < itemResponses.length; j++) {
      var questionTitle = itemResponses[j].getItem().getTitle().toLowerCase();
      var answer = itemResponses[j].getResponse().trim();
      
      if (questionTitle.includes("email")) {
        email = answer.toLowerCase();
      } else if (questionTitle.includes("link")) {
        targetLink = answer;
      }
    }
    
    // If we have both an email and a link, add them to our lists
    if (email !== "" && targetLink !== "") {
      usersToNotify.push({ email: email, link: targetLink });
      uniqueLinksToCheck[targetLink] = false; // Default status is false (button not found yet)
    }
  }

  // 4. Fetch the HTML for each unique link requested by users
  for (var link in uniqueLinksToCheck) {
    try {
      var targetResponse = UrlFetchApp.fetch(link);
      var targetHtml = targetResponse.getContentText().toLowerCase();
      
      if (targetHtml.includes('id="apply-action-button"')) {
        uniqueLinksToCheck[link] = true;
      }
    } catch (error) {
      console.log("Failed to fetch link: " + link + ". Error: " + error.message);
    }
  }

  // 5. Check tracking properties and send emails
  var scriptProperties = PropertiesService.getScriptProperties();
  var sentLogString = scriptProperties.getProperty('sentNotifications');
  var sentNotifications = sentLogString ? JSON.parse(sentLogString) : {};
  
  var emailsSentCount = 0;

  for (var k = 0; k < usersToNotify.length; k++) {
    var userEmail = usersToNotify[k].email;
    var userLink = usersToNotify[k].link;
    
    // Create a unique tracking key combining their email and the specific job link
    var trackingKey = userEmail + "|" + userLink;
    
    // If the button was found AND we haven't emailed this person about this specific link yet
    if (uniqueLinksToCheck[userLink] === true && !sentNotifications[trackingKey]) {
      
      var subject = "Google Career Application is Open";
      var message = "Please apply to the Google position you are watching. The apply button has been updated on the website:\n\n" + userLink;
      
      MailApp.sendEmail(userEmail, subject, message);
      
      // Mark this specific email and link combination as sent
      sentNotifications[trackingKey] = true;
      emailsSentCount++;
    }
  }
  
  // 6. Save the updated tracking list back to the properties
  if (emailsSentCount > 0) {
    scriptProperties.setProperty('sentNotifications', JSON.stringify(sentNotifications));
    console.log("Successfully sent " + emailsSentCount + " new emails.");
  } else {
    console.log("No new updates to send.");
  }
}

/**
 * Run this function manually to test if the script reads the new form format correctly.
 * It will NOT send any emails.
 */
function testReadMultipleLinks() {
  var formId = "1JzQkcc8fsnolMbQLZoRrooxmFt-onrWVa8gEPlqnbs4"; 
  var form = FormApp.openById(formId);
  var responses = form.getResponses();
  
  console.log("Test: Found " + responses.length + " total submissions.");
  
  for (var i = 0; i < responses.length; i++) {
    var itemResponses = responses[i].getItemResponses();
    var email = "Not found";
    var link = "Not found";
    
    for (var j = 0; j < itemResponses.length; j++) {
      var questionTitle = itemResponses[j].getItem().getTitle().toLowerCase();
      
      if (questionTitle.includes("email")) {
        email = itemResponses[j].getResponse();
      } else if (questionTitle.includes("link")) {
        link = itemResponses[j].getResponse();
      }
    }
    
    console.log("Row " + (i + 1) + " | Email: " + email + " | Link: " + link);
  }
}

/**
 * Run this to reset the script if you need to test it again.
 */
function resetScript() {
  var scriptProperties = PropertiesService.getScriptProperties();
  scriptProperties.deleteProperty('sentNotifications');
  console.log("Script reset. The tracking log is clear.");
}