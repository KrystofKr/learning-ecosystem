"""Structured PLAN and REVIEW contracts for synthesis editors."""

def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


STR={'type':'string'}
RANGE={'start':STR,'end':STR,'quote':STR}
PLAN_SCHEMA=obj({
    'summary':STR,
    'coverage':{'type':'array','items':obj({'topic':STR,'decision':{'type':'string','enum':['added','existing','excluded']},
        'section':STR,'reason':STR,'addition_index':{'type':'integer'},**RANGE})},
    'additions':{'type':'array','items':obj({'section':STR,'text':STR,**RANGE})},
})
REVIEW_SCHEMA=obj({'approved':{'type':'boolean'},'reason':STR,
    'problems':{'type':'array','items':STR},'missing_important':{'type':'array','items':STR}})
