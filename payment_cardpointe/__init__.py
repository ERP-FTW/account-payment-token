from . import models
from . import controllers
from odoo.addons.payment import setup_provider

def post_init_hook(env):
    setup_provider(env, 'cardpointe')
