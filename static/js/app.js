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
            const suggestedPartPrice = materialCost + hours * priceFactor;
            total += rowCost;
            materialTotal += materialCost;
            hoursTotal += hours;
            row.querySelector('[data-part-cost]').textContent = euro.format(rowCost);
            row.querySelector('[data-part-suggested-price]').textContent = euro.format(suggestedPartPrice);
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
    const showArchived = document.querySelector('#show-archived');
    const totalOutput = document.querySelector('[data-live-total]');
    const profitOutput = document.querySelector('[data-live-profit]');
    const defaults = window.saleDefaults || {};
    const costs = window.saleCosts || {};
    const partCosts = window.salePartCosts || {};
    const modelCategories = window.saleModelCategories || {};
    const archivedModels = new Set(window.saleArchivedModelIds || []);
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

    const selectedPartCost = (partId, materialId, printerId, fallback) => {
        const choices = partCosts[partId] || {};
        return Number(choices[`${materialId || 0}:${printerId || 0}`] ?? fallback ?? 0);
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
            const part = row.querySelector('.item-part').selectedOptions[0];
            const cost = part?.value
                ? selectedPartCost(part.value, materialId, printerId, part.dataset.cost)
                : selectedCost(model.value, materialId, printerId);
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

    const filterModels = (row) => {
        const model = row.querySelector('.item-model');
        const category = row.querySelector('.item-category');
        let clearSelection = false;
        [...model.options].forEach((option) => {
            if (!option.value) return;
            const categoryMatches = !category.value
                || (modelCategories[option.value] || []).some((id) => String(id) === category.value);
            const archiveMatches = showArchived.checked || !archivedModels.has(option.value);
            const visible = categoryMatches && archiveMatches;
            option.hidden = !visible;
            option.disabled = !visible;
            if (option.selected && !visible) clearSelection = true;
        });
        if (clearSelection) {
            model.value = '';
            row.querySelector('.item-part').value = '';
            row.querySelector('.item-price').value = '0';
        }
        filterParts(row);
    };

    const filterParts = (row) => {
        const modelId = row.querySelector('.item-model').value;
        const part = row.querySelector('.item-part');
        [...part.options].forEach((option) => {
            if (!option.value) return;
            const visible = option.dataset.model === modelId;
            option.hidden = !visible;
            option.disabled = !visible;
            if (!visible && option.selected) part.value = '';
        });
    };

    const updateModel = (row) => {
        const model = row.querySelector('.item-model');
        const selected = model.selectedOptions[0];
        const modelDefaults = defaults[model.value];
        row.querySelector('.item-part').value = '';
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
        filterParts(row);
        updateOrder();
    };

    const updatePart = (row) => {
        const part = row.querySelector('.item-part').selectedOptions[0];
        const model = row.querySelector('.item-model').selectedOptions[0];
        row.querySelector('.item-price').value = part?.value
            ? part.dataset.price || '0'
            : model?.dataset.price || '0';
        updateOrder();
    };

    saleItems.querySelectorAll('[data-sale-item]').forEach((row) => {
        filterModels(row);
        filterParts(row);
    });
    saleItems.addEventListener('change', (event) => {
        const row = event.target.closest('[data-sale-item]');
        if (!row) return;
        if (event.target.matches('.item-category')) filterModels(row);
        else if (event.target.matches('.item-model')) updateModel(row);
        else if (event.target.matches('.item-part')) updatePart(row);
        else updateOrder();
    });
    showArchived.addEventListener('change', () => {
        saleItems.querySelectorAll('[data-sale-item]').forEach(filterModels);
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
        newItem.querySelector('.item-category').value = '';
        newItem.querySelector('.item-part').value = '';
        newItem.querySelector('.item-material').value = '';
        newItem.querySelector('.item-printer').value = '';
        newItem.querySelector('[name="item_customer_note"]').value = '';
        newItem.querySelector('[data-item-default-note]').textContent = 'Modell wählen, um Standardwerte anzuzeigen.';
        saleItems.append(newItem);
        filterModels(newItem);
        updateOrder();
    });
    shipping.addEventListener('change', updateOrder);
    packaging.addEventListener('input', updateOrder);
    updateOrder();
}

const modelOverview = document.querySelector('[data-model-overview]');
if (modelOverview) {
    const header = modelOverview.querySelector('.data-head');
    let activeSortKey = null;
    let sortAscending = false;

    header.addEventListener('click', (event) => {
        const button = event.target.closest('[data-sort-key]');
        if (!button) return;

        const { sortKey, sortType } = button.dataset;
        if (sortKey === activeSortKey) sortAscending = !sortAscending;
        else {
            activeSortKey = sortKey;
            sortAscending = false;
        }

        const rows = [...modelOverview.querySelectorAll('.data-row')];
        const columnIndex = [...header.children].indexOf(button.parentElement);
        const originalOrder = new Map(rows.map((row, index) => [row, index]));
        rows.sort((a, b) => {
            const valueA = a.children[columnIndex].dataset.sortValue;
            const valueB = b.children[columnIndex].dataset.sortValue;
            const comparison = sortType === 'number'
                ? Number(valueA) - Number(valueB)
                : valueA.localeCompare(valueB, 'de', { numeric: true, sensitivity: 'base' });
            return (sortAscending ? comparison : -comparison) || originalOrder.get(a) - originalOrder.get(b);
        });
        rows.forEach((row) => modelOverview.append(row));

        header.querySelectorAll('[data-sort-key]').forEach((sortButton) => {
            const isActive = sortButton === button;
            sortButton.querySelector('.sort-indicator').textContent = isActive
                ? (sortAscending ? '↑' : '↓')
                : '';
            sortButton.setAttribute(
                'aria-label',
                isActive
                    ? `Nach ${sortButton.dataset.sortLabel} ${sortAscending ? 'aufsteigend' : 'absteigend'} sortiert`
                    : `Nach ${sortButton.dataset.sortLabel} sortieren`
            );
        });
    });
}
