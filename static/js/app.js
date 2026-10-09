const euro = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' });
const numberValue = (input) => Number.parseFloat((input?.value || '0').replace(',', '.')) || 0;

const partList = document.querySelector('[data-part-list]');
if (partList) {
    const template = document.querySelector('#part-template');
    const addButton = document.querySelector('[data-add-part]');
    const totalOutput = document.querySelector('[data-model-total]');
    const recommendedOutput = document.querySelector('[data-recommended-price]');
    const extraMaterialCost = document.querySelector('[name="additional_material_cost"]');
    const extraWorkHours = document.querySelector('[name="additional_work_hours"]');
    const extraHourlyCost = document.querySelector('[name="additional_hourly_cost"]');
    const priceFactor = Number(window.priceCalculatorFactor) || 3;

    const updateParts = () => {
        let total = 0;
        let materialTotal = 0;
        let hoursTotal = 0;
        const rows = [...partList.querySelectorAll('[data-part-row]')];
        rows.forEach((row, index) => {
            row.querySelector('[data-part-number]').textContent = String(index + 1).padStart(2, '0');
            const material = row.querySelector('[name="material_id"]');
            const printer = row.querySelector('[name="printer_id"]');
            const grams = numberValue(row.querySelector('[name="weight_g"]'));
            const hours = numberValue(row.querySelector('[name="print_hours"]'))
                + numberValue(row.querySelector('[name="print_minutes"]')) / 60;
            const materialCost = grams * (Number(material.selectedOptions[0]?.dataset.price) || 0) / 1000;
            const printerCost = hours * (Number(printer.selectedOptions[0]?.dataset.rate) || 0);
            const rowCost = materialCost + printerCost;
            total += rowCost;
            materialTotal += materialCost;
            hoursTotal += hours;
            row.querySelector('[data-part-cost]').textContent = euro.format(rowCost);
        });
        const additionalCost = numberValue(extraMaterialCost)
            + numberValue(extraWorkHours) * numberValue(extraHourlyCost);
        totalOutput.textContent = euro.format(total + additionalCost);
        recommendedOutput.textContent = euro.format(materialTotal + hoursTotal * priceFactor + additionalCost);
    };

    partList.addEventListener('input', updateParts);
    partList.addEventListener('change', updateParts);
    [extraMaterialCost, extraWorkHours, extraHourlyCost].forEach((input) => {
        input.addEventListener('input', updateParts);
        input.addEventListener('change', updateParts);
    });
    partList.addEventListener('click', (event) => {
        const remove = event.target.closest('[data-remove-part]');
        if (remove && partList.querySelectorAll('[data-part-row]').length > 1) {
            remove.closest('[data-part-row]').remove();
            updateParts();
        }
    });
    addButton.addEventListener('click', () => {
        partList.append(template.content.cloneNode(true));
        updateParts();
    });
    updateParts();
}

const saleItems = document.querySelector('[data-sale-items]');
if (saleItems) {
    const itemTemplate = saleItems.firstElementChild.cloneNode(true);
    const addItem = document.querySelector('[data-add-item]');
    const shipping = document.querySelector('#shipping-method');
    const packaging = document.querySelector('#packaging-cost');
    const totalOutput = document.querySelector('[data-live-total]');
    const profitOutput = document.querySelector('[data-live-profit]');
    const defaults = window.saleDefaults || {};
    const costs = window.saleCosts || {};
    const customerName = document.querySelector('[name="customer_name"]');
    const knownCustomers = document.querySelector('#known-customer-names');

    customerName.addEventListener('input', () => {
        const match = [...knownCustomers.options].find((option) =>
            option.value.trim().toLocaleLowerCase() === customerName.value.trim().toLocaleLowerCase());
        if (!match) return;
        document.querySelector('[name="company_name"]').value = match.dataset.company || '';
        document.querySelector('[name="street"]').value = match.dataset.street || '';
        document.querySelector('[name="postal_code"]').value = match.dataset.postalCode || '';
        document.querySelector('[name="city"]').value = match.dataset.city || '';
    });

    const selectedCost = (modelId, materialId, printerId) => {
        const choices = costs[modelId] || {};
        return Number(choices[`${materialId || 0}:${printerId || 0}`] ?? choices['0:0'] ?? 0);
    };

    const updateOrder = () => {
        let orderTotal = Number(shipping.selectedOptions[0]?.dataset.price) || 0;
        let orderProfit = -numberValue(packaging);
        saleItems.querySelectorAll('[data-sale-item]').forEach((row, index) => {
            row.querySelector('[data-item-number]').textContent = String(index + 1).padStart(2, '0');
            const model = row.querySelector('.item-model');
            const quantity = Math.max(1, numberValue(row.querySelector('.item-quantity')));
            const price = numberValue(row.querySelector('.item-price'));
            const materialId = row.querySelector('.item-material').value;
            const printerId = row.querySelector('.item-printer').value;
            const cost = selectedCost(model.value, materialId, printerId);
            const lineTotal = quantity * price;
            orderTotal += lineTotal;
            orderProfit += quantity * (price - cost);
            row.querySelector('[data-item-cost]').textContent = euro.format(cost);
            const profitOutput = row.querySelector('[data-item-profit]');
            profitOutput.textContent = euro.format(quantity * (price - cost));
            profitOutput.classList.toggle('negative', quantity * (price - cost) < 0);
            profitOutput.classList.toggle('positive', quantity * (price - cost) >= 0);
            row.querySelector('[data-item-total]').textContent = euro.format(lineTotal);
        });
        totalOutput.textContent = euro.format(orderTotal);
        profitOutput.textContent = euro.format(orderProfit);
        profitOutput.classList.toggle('negative', orderProfit < 0);
        profitOutput.classList.toggle('positive', orderProfit >= 0);
    };

    const updateModel = (row) => {
        const model = row.querySelector('.item-model');
        const selected = model.selectedOptions[0];
        const modelDefaults = defaults[model.value];
        row.querySelector('.item-price').value = selected?.dataset.price || '0';
        row.querySelector('.item-material').value = '';
        row.querySelector('.item-printer').value = '';
        const note = row.querySelector('[data-item-default-note]');
        if (modelDefaults) {
            const filament = modelDefaults.materials.map((item) => item.name).join(', ') || 'nicht hinterlegt';
            const printer = modelDefaults.printers.map((item) => item.name).join(', ') || 'nicht hinterlegt';
            note.textContent = `Standardfilament: ${filament} · Drucker: ${printer}`;
        } else {
            note.textContent = 'Modell wählen, um Standardwerte anzuzeigen.';
        }
        updateOrder();
    };

    saleItems.addEventListener('change', (event) => {
        const row = event.target.closest('[data-sale-item]');
        if (row && event.target.matches('.item-model')) updateModel(row);
        else updateOrder();
    });
    saleItems.addEventListener('input', updateOrder);
    saleItems.addEventListener('click', (event) => {
        const remove = event.target.closest('[data-remove-item]');
        if (remove && saleItems.querySelectorAll('[data-sale-item]').length > 1) {
            remove.closest('[data-sale-item]').remove();
            updateOrder();
        }
    });
    addItem.addEventListener('click', () => {
        const newItem = itemTemplate.cloneNode(true);
        newItem.querySelector('.item-model').value = '';
        newItem.querySelector('.item-quantity').value = '1';
        newItem.querySelector('.item-price').value = '0';
        newItem.querySelector('.item-material').value = '';
        newItem.querySelector('.item-printer').value = '';
        newItem.querySelector('[data-item-default-note]').textContent = 'Modell wählen, um Standardwerte anzuzeigen.';
        saleItems.append(newItem);
        updateOrder();
    });
    shipping.addEventListener('change', updateOrder);
    packaging.addEventListener('input', updateOrder);
    updateOrder();
}
