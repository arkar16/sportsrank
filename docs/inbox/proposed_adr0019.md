Proposed solution for ADR-0019, and fixes that needs to be added for success.

We need to track CORS model now from ranking, to true model predictions.

This means two things:

The focus needs to be on improving CORS’s prediction ability, since I view CORS as meaning to be a predictive strength model, rather than just a resume ranking model.

- this is what I would like to see
  - firstly, we need to think about the overall current state of tracking for spreads and stuff. Like, I know we publish them, but we need to analyze predictions after the fact. Like this is a P0 state of issue, where we do not have any tracking rn of how the model did. We pull in scores, but we need to accurately score how the model performs in terms of picking favorites.
  - what I am saying is when I say straight up and against the spread is that we need to judge how CORS does in picking the winner, and also against the spread. But remember that spread is currently displayed in home team terms, if that makes sense
  - so App State vs Georgia State, App State is home
    - App State +2 is really just saying that Georgia State is favored by 2 points by CORS
      - therefore, these are the possible states of the model after the game (FOR CORS review only)
        - Georgia State loses
          - CORS straight up is wrong
          - CORS ats is wrong
        - Georgia State wins by exactly 2
          - this is where we need to add hooks to CORS modeling I think, but it doesn’t need to be by .5, I am just saying it can’t land on exactly 2.0
          - but anyways, this would be a push as of now, so ATS is a push
          - but CORS straight up is correct
        - State wins by more than 2
          - CORS ATS is correct
          - CORS straight up is correctly
      - let me know if I messed any of this analysis is wrong
    - so this is the current state, if that makes sense.
      - if we add the bookmaker odds, then we can track performance against the market
        - this is where it would get interesting
        - lets say we have GSU -2, but ESPN has it as GSU +2
          - then we can compare our model’s prediction versus the bookmakers odds if that makes sense
  - look at inbox/CORS the excel file. There is a lot of features that never made it to the website that I would like to see.