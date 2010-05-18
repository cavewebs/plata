from decimal import Decimal

from django import forms
from django.contrib import messages
from django.core.urlresolvers import reverse
from django.forms.models import inlineformset_factory, modelform_factory
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render_to_response
from django.template import RequestContext
from django.utils.translation import ugettext as _

import plata


class Shop(object):
    def __init__(self, product_model, contact_model, order_model):
        self.product_model = product_model
        self.contact_model = contact_model
        self.order_model = order_model
        self.orderitem_model = self.order_model.items.related.model

        plata.register(self)

    def get_urls(self):
        return self.get_product_urls() + self.get_shop_urls() + self.get_blabla_urls()

    def get_product_urls(self):
        from django.conf.urls.defaults import patterns, url

        product_dict = {
            'queryset': self.product_model.objects.all(),
            }

        return patterns('django.views.generic',
            url(r'^$', lambda request: redirect('plata_product_list')),
            url(r'^products/$', 'list_detail.object_list', dict(
                product_dict,
                paginate_by=20,
                ), name='plata_product_list'),
            url(r'^products/(?P<object_id>\d+)/$', self.product_detail, product_dict, name='plata_product_detail'),
            )

    def get_shop_urls(self):
        from django.conf.urls.defaults import patterns, url
        return patterns('',
            url(r'^cart/$', self.cart, name='plata_shop_cart'),
            url(r'^checkout/$', self.checkout, name='plata_shop_checkout'),
            url(r'^confirmation/$', self.confirmation, name='plata_shop_confirmation'),
            )

    def get_blabla_urls(self):
        from django.conf.urls.defaults import patterns, url
        return patterns('',
            url(r'^pdf/(?P<order_id>\d+)/$', self.blabla_pdf, name='plata_blabla_pdf'),
            )

    @property
    def urls(self):
        return self.get_urls()

    def default_currency(self, request):
        return 'CHF'

    def clear_session(self, request):
        for key in ('shop_contact', 'shop_order'):
            if key in request.session:
                del request.session[key]

    def order_from_request(self, request, create=False):
        try:
            return self.order_model.objects.get(pk=request.session.get('shop_order'))
        except (ValueError, self.order_model.DoesNotExist):
            if create:
                contact = self.contact_from_request(request, create)
                order = self.order_model.objects.create(
                    contact=contact,
                    currency=contact.currency,
                    )
                request.session['shop_order'] = order.pk
                return order

        return None

    def contact_from_request(self, request, create=False):
        try:
            return self.contact_model.objects.get(pk=request.session.get('shop_contact'))
        except (ValueError, self.contact_model.DoesNotExist):
            pass

        if request.user.is_authenticated():
            try:
                contact = self.contact_model.objects.get(user=request.user)
                request.session['shop_contact'] = contact.pk
                return contact
            except (self.contact_model.DoesNotExist, self.contact_model.MultipleObjectsReturned):
                pass

        if create:
            initial = {
                'shipping_same_as_billing': True,
                'currency': self.default_currency(request),
                }

            if request.user.is_authenticated():
                initial.update({
                    'billing_first_name': request.user.first_name,
                    'billing_last_name': request.user.last_name,
                    'email': request.user.email,
                    'user': request.user,
                })

            contact = self.contact_model.objects.create(**initial)
            request.session['shop_contact'] = contact.pk
            return contact

        return None

    def get_context(self, request, context):
        instance = RequestContext(request) #, self.get_extra_context(request))
        instance.update(context)
        return instance

    def product_detail(self, request, *args, **kwargs):
        p = get_object_or_404(self.product_model, pk=kwargs.get('object_id'))
        form_class = self.order_modify_item_form(request, p)

        if request.method == 'POST':
            form = form_class(request.POST)

            if form.is_valid():
                order = self.order_from_request(request, create=True)

                order.modify_item(
                    form.cleaned_data.get('variation'),
                    form.cleaned_data.get('quantity'),
                    )
                order.recalculate_total()

                messages.success(request, 'Successfully updated cart.')
                return HttpResponseRedirect('.')
        else:
            form = form_class()

        return render_to_response('product/product_detail.html', self.get_context(request, {
            'form': form,
            }))

    def order_modify_item_form(self, request, product):
        class Form(forms.Form):
            quantity = forms.IntegerField(label=_('quantity'), initial=1)

            def __init__(self, *args, **kwargs):
                super(Form, self).__init__(*args, **kwargs)
                for group in product.option_groups.all():
                    self.fields['option_%s' % group.id] = forms.ModelChoiceField(
                        queryset=group.options.all(),
                        label=group.name)

            def clean(self):
                data = super(Form, self).clean()

                options = [data.get('option_%s' % group.id) for group in product.option_groups.all()]

                if all(options):
                    variations = product.variations.all()

                    for group in product.option_groups.all():
                        variations = variations.filter(options=self.cleaned_data.get('option_%s' % group.id))

                    data['variation'] = variations.get()

                return data
        return Form

    def cart(self, request):
        order = self.order_from_request(request, create=False)

        if not order:
            return self.render_cart_empty(request, {})

        OrderItemFormset = inlineformset_factory(
            self.order_model,
            self.orderitem_model,
            extra=0,
            fields=('quantity',),
            )

        if request.method == 'POST':
            formset = OrderItemFormset(request.POST, instance=order)

            if formset.is_valid():
                changed = False
                for form in formset.forms:
                    if formset._should_delete_form(form):
                        order.modify_item(form.instance.variation,
                            absolute=0,
                            recalculate=False)
                        changed = True
                    elif form.has_changed():
                        order.modify_item(form.instance.variation,
                            absolute=form.cleaned_data['quantity'],
                            recalculate=False)
                        changed = True

                if changed:
                    order.recalculate_total()
                    messages.success(request, _('The cart has been updated.'))

                if 'checkout' in request.POST:
                    return redirect('plata_shop_checkout')
                return HttpResponseRedirect('.')
        else:
            formset = OrderItemFormset(instance=order)

        return self.render_cart(request, {
            'order': order,
            'orderitemformset': formset,
            'empty': request.GET.get('empty', False), # Whether the cart is empty.
                                                      # Flag gets set by checkout view.
            })

    def render_cart_empty(self, request, context):
        context.update({'empty': True})

        return render_to_response('plata/shop_cart.html',
            self.get_context(request, context))

    def render_cart(self, request, context):
        return render_to_response('plata/shop_cart.html',
            self.get_context(request, context))

    def checkout(self, request):
        order = self.order_from_request(request, create=False)

        if not order:
            return HttpResponseRedirect(reverse('plata_shop_cart') + '?empty=1')

        ContactForm = modelform_factory(self.contact_model,
            exclude=('user', 'created', 'notes', 'currency'))
        OrderForm = modelform_factory(self.order_model, fields=('notes',))

        if request.method == 'POST':
            c_form = ContactForm(request.POST, prefix='contact', instance=order.contact)
            o_form = OrderForm(request.POST, prefix='order', instance=order)

            if c_form.is_valid() and o_form.is_valid():
                c_form.save()
                order = o_form.save()
                order.copy_address()
                order.save()

                if order.status < self.order_model.CHECKOUT:
                    order.update_status(self.order_model.CHECKOUT, 'Checkout completed')

                return redirect('plata_shop_confirmation')
        else:
            c_form = ContactForm(instance=order.contact, prefix='contact')
            o_form = OrderForm(instance=order, prefix='order')

        return self.render_checkout(request, {
            'order': order,
            'contactform': c_form,
            'orderform': o_form,
            })

    def render_checkout(self, request, context):
        return render_to_response('plata/shop_checkout.html',
            self.get_context(request, context))

    def confirmation(self, request):
        order = self.order_from_request(request, create=False)

        if not order:
            return HttpResponseRedirect(reverse('plata_shop_cart') + '?empty=1')

        if request.method == 'POST':
            # TODO any validation or data necessary here?
            order.update_status(self.order_model.CONFIRMED, 'Confirmation given')

        return self.render_confirmation(request, {'order': order})

    def render_confirmation(self, request, context):
        return render_to_response('plata/shop_confirmation.html',
            self.get_context(request, context))

    def blabla_pdf(self, request, order_id):
        order = get_object_or_404(self.order_model, pk=order_id)

        order.shipping_cost = 8 / Decimal('1.076')
        order.shipping_discount = 0
        order.recalculate_total(save=False)

        from pdfdocument.document import PDFDocument, cm, mm
        from pdfdocument.elements import create_stationery_fn, MetronomStationery
        from pdfdocument.utils import pdf_response

        pdf, response = pdf_response('order-%09d' % order.id)
        pdf.init_letter(page_fn=create_stationery_fn(MetronomStationery()))

        pdf.address_head(u'FEINHEIT GmbH - Molkenstrasse 21 - CH-8004 Z\374rich')
        pdf.address(order, 'billing_')
        pdf.next_frame()

        pdf.h1('Order %09d' % order.id)

        pdf.table([(
                'Product',
                'Quantity',
                'Unit price',
                'Line item price',
            )]+[
            (
                unicode(item.variation),
                item.quantity,
                u'%.2f' % item.unit_price,
                u'%.2f' % item.discounted_subtotal,
            ) for item in order.items.all()],
            (8*cm, 1*cm, 3*cm, 4.4*cm), pdf.style.tableHead)

        summary_table = [
            ('', ''),
            ('Subtotal', u'%.2f' % order.subtotal),
            ]

        if order.discount:
            summary_table.append(('Discount', u'%.2f' % order.discount))

        if order.shipping:
            summary_table.append(('Shipping', u'%.2f' % order.shipping))

        pdf.table(summary_table, (12*cm, 4.4*cm), pdf.style.table)

        pdf.spacer(1*mm)
        pdf.table([
            ('Total', u'%.2f' % order.total),
            ], (12*cm, 4.4*cm), pdf.style.tableHead)

        pdf.generate()
        return response
